from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path


class WriteQueue:
    """Per-path asyncio lock manager."""

    def __init__(self) -> None:
        self._locks: dict[Path, asyncio.Lock] = {}

    async def acquire(self, path: Path) -> None:
        """Acquire the lock for the given path."""
        canonical = path.resolve()  # noqa: ASYNC240
        if canonical not in self._locks:
            self._locks[canonical] = asyncio.Lock()
        await self._locks[canonical].acquire()

    def release(self, path: Path) -> None:
        """Release the lock for the given path."""
        canonical = path.resolve()
        lock = self._locks.get(canonical)
        if lock is not None:
            lock.release()

    @asynccontextmanager
    async def lock(self, path: Path) -> AsyncIterator[None]:
        """Async context manager that acquires on enter and releases on exit."""
        await self.acquire(path)
        try:
            yield
        finally:
            self.release(path)
