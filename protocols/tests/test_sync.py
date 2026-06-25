from __future__ import annotations

import asyncio
import threading

import pytest
from orxtra.protocols._sync import run_sync


async def _async_add(a: int, b: int) -> int:
    return a + b


class TestRunSync:
    def test_no_loop_uses_asyncio_run(self) -> None:
        """Call from sync context with no event loop -- uses asyncio.run()."""
        result = run_sync(_async_add(3, 4))
        assert result == 7

    def test_same_thread_raises(self) -> None:
        """Call from inside asyncio.run() on the same thread -- RuntimeError."""

        async def _inner() -> None:
            run_sync(_async_add(1, 2))

        with pytest.raises(RuntimeError, match="run_sync\\(\\) called from the event loop thread"):
            asyncio.run(_inner())

    def test_different_thread_uses_threadsafe(self) -> None:
        """Start a loop in a background thread, call run_sync from main thread."""
        loop = asyncio.new_event_loop()
        thread = threading.Thread(target=loop.run_forever, daemon=True)
        thread.start()

        try:
            result = run_sync(_async_add(10, 20))
            assert result == 30
        finally:
            loop.call_soon_threadsafe(loop.stop)
            thread.join(timeout=5)
            loop.close()
