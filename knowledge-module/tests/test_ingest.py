from __future__ import annotations

import hashlib
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.knowledge_module._types import KnowledgeConfig


def _make_config() -> KnowledgeConfig:
    return KnowledgeConfig(
        db_url="postgresql://localhost/test",
        cognify_model="gpt-4o-mini",
        cognify_api_key="sk-test-key",
        max_retrieval_results=10,
    )


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _make_pool(fetchval_return: str | None = None) -> MagicMock:
    """Create a mock pool with fetchval and execute for ContentHashCache."""
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=fetchval_return)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def mock_cognee() -> MagicMock:
    mock = MagicMock()
    mock.add = AsyncMock()
    mock.cognify = AsyncMock()
    mock.config = MagicMock()
    mock.config.set_llm_config = MagicMock()
    mock.config.set_vector_db_config = MagicMock()
    return mock


@pytest.fixture(autouse=True)
def _reset_ingest_cache() -> None:
    from orxtra.knowledge_module import _ingest  # noqa: PLC0415

    _ingest._cache = None  # noqa: SLF001


class TestIngestLessons:
    @pytest.mark.asyncio
    async def test_ingest_calls_cognee(self, mock_cognee: MagicMock) -> None:
        pool = _make_pool(fetchval_return=None)
        lessons = [{"id": "1", "content": "lesson one"}]
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_lessons(_make_config(), lessons, pool)
        assert count == 1
        mock_cognee.add.assert_awaited_once_with("lesson one")
        mock_cognee.cognify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_lessons_returns_zero(self, mock_cognee: MagicMock) -> None:
        pool = _make_pool()
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_lessons(_make_config(), [], pool)
        assert count == 0
        mock_cognee.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cognee_error_propagated(self, mock_cognee: MagicMock) -> None:
        pool = _make_pool(fetchval_return=None)
        mock_cognee.cognify = AsyncMock(side_effect=RuntimeError("cognee failed"))
        lessons = [{"id": "1", "content": "lesson one"}]
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            with pytest.raises(RuntimeError, match="cognee failed"):
                await _ingest.ingest_lessons(_make_config(), lessons, pool)

    @pytest.mark.asyncio
    async def test_hash_prevents_reingestion(self, mock_cognee: MagicMock) -> None:
        pool = _make_pool(fetchval_return=None)
        lessons = [{"id": "1", "content": "same content"}]
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            count1 = await _ingest.ingest_lessons(_make_config(), lessons, pool)
            assert count1 == 1
            mock_cognee.add.reset_mock()
            mock_cognee.cognify.reset_mock()
            # After first ingest, the hash is stored. Simulate DB returning the hash.
            pool.fetchval = AsyncMock(return_value=_sha256("same content"))
            count2 = await _ingest.ingest_lessons(_make_config(), lessons, pool)
            assert count2 == 0
            mock_cognee.add.assert_not_awaited()


class TestIngestFromPool:
    @pytest.mark.asyncio
    async def test_reads_from_pool_and_ingests(self, mock_cognee: MagicMock) -> None:
        mock_row = {"id": "1", "content": "db lesson", "tags": ["t"], "permanent": True}
        mock_conn = MagicMock()
        mock_conn.fetch = AsyncMock(return_value=[mock_row])
        mock_pool = MagicMock()
        mock_pool.acquire = MagicMock()
        mock_pool.acquire.return_value.__aenter__ = AsyncMock(return_value=mock_conn)
        mock_pool.acquire.return_value.__aexit__ = AsyncMock(return_value=False)
        # Add fetchval/execute for ContentHashCache
        mock_pool.fetchval = AsyncMock(return_value=None)
        mock_pool.execute = AsyncMock()

        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_from_pool(_make_config(), mock_pool)
        assert count == 1
        mock_conn.fetch.assert_awaited_once()
