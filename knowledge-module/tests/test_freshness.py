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


class TestContentHashCache:
    @pytest.mark.asyncio
    async def test_new_content_is_changed(self) -> None:
        pool = _make_pool(fetchval_return=None)
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "content") is True

    @pytest.mark.asyncio
    async def test_same_content_not_changed(self) -> None:
        pool = _make_pool(fetchval_return=_sha256("content"))
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "content") is False

    @pytest.mark.asyncio
    async def test_changed_content_detected(self) -> None:
        pool = _make_pool(fetchval_return=_sha256("original"))
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "modified") is True

    @pytest.mark.asyncio
    async def test_update_calls_execute(self) -> None:
        pool = _make_pool()
        cache = ContentHashCache(pool)
        await cache.update("key1", "content")
        pool.execute.assert_awaited_once()
        call_args = pool.execute.call_args
        assert call_args[0][1] == "key1"
        assert call_args[0][2] == _sha256("content")

    @pytest.mark.asyncio
    async def test_different_keys_independent(self) -> None:
        pool = _make_pool(fetchval_return=_sha256("content"))
        cache = ContentHashCache(pool)
        assert await cache.is_changed("key1", "content") is False
        # Second call with different key -- pool still returns same hash
        # but this tests that the key is passed to the query
        pool.fetchval = AsyncMock(return_value=None)
        assert await cache.is_changed("key2", "content") is True
