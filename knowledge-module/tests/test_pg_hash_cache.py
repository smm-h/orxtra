from __future__ import annotations

import hashlib
from unittest.mock import AsyncMock, MagicMock

import pytest
from orxt.knowledge_module._freshness import ContentHashCache


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_pool(fetchval_return: str | None = None) -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock()
    return pool


class TestPgHashCache:
    @pytest.mark.asyncio
    async def test_is_changed_new_key(self) -> None:
        pool = _make_pool(fetchval_return=None)
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "content") is True
        pool.fetchval.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_is_changed_same_hash(self) -> None:
        pool = _make_pool(fetchval_return=_sha256("content"))
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "content") is False

    @pytest.mark.asyncio
    async def test_is_changed_different_hash(self) -> None:
        pool = _make_pool(fetchval_return=_sha256("old content"))
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "new content") is True

    @pytest.mark.asyncio
    async def test_update_inserts(self) -> None:
        pool = _make_pool()
        cache = ContentHashCache(pool)
        await cache.update("key1", "content")
        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args[0]
        assert "INSERT INTO knowledge_hashes" in call_args[0]
        assert "ON CONFLICT" in call_args[0]
        assert call_args[1] == "key1"
        assert call_args[2] == _sha256("content")
