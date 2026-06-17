from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg


class ContentHashCache:
    """PG-backed content hash cache using the knowledge_hashes table."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    def _hash(self, content: str) -> str:
        return hashlib.sha256(content.encode()).hexdigest()

    async def is_changed(self, key: str, content: str) -> bool:
        stored = await self._pool.fetchval(
            "SELECT hash FROM knowledge_hashes WHERE key = $1",
            key,
        )
        return bool(stored != self._hash(content))

    async def update(self, key: str, content: str) -> None:
        h = self._hash(content)
        await self._pool.execute(
            """INSERT INTO knowledge_hashes (key, hash)
               VALUES ($1, $2)
               ON CONFLICT (key) DO UPDATE SET hash = $2, updated_at = now()""",
            key,
            h,
        )
