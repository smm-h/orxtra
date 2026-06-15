from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxt.knowledge_module._types import KnowledgeConfig


def _make_config() -> KnowledgeConfig:
    return KnowledgeConfig(
        db_url="postgresql://localhost/test",
        cognify_model="gpt-4o-mini",
        cognify_api_key="sk-test-key",
        max_retrieval_results=10,
    )


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
    from orxt.knowledge_module import _ingest  # noqa: PLC0415
    from orxt.knowledge_module._freshness import ContentHashCache  # noqa: PLC0415

    _ingest._cache = ContentHashCache()  # noqa: SLF001


class TestIngestLessons:
    @pytest.mark.asyncio
    async def test_ingest_calls_cognee(self, mock_cognee: MagicMock) -> None:
        lessons = [{"id": "1", "content": "lesson one"}]
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_lessons(_make_config(), lessons)
        assert count == 1
        mock_cognee.add.assert_awaited_once_with("lesson one")
        mock_cognee.cognify.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_empty_lessons_returns_zero(self, mock_cognee: MagicMock) -> None:
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_lessons(_make_config(), [])
        assert count == 0
        mock_cognee.add.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_cognee_error_propagated(self, mock_cognee: MagicMock) -> None:
        mock_cognee.cognify = AsyncMock(side_effect=RuntimeError("cognee failed"))
        lessons = [{"id": "1", "content": "lesson one"}]
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            with pytest.raises(RuntimeError, match="cognee failed"):
                await _ingest.ingest_lessons(_make_config(), lessons)

    @pytest.mark.asyncio
    async def test_hash_prevents_reingestion(self, mock_cognee: MagicMock) -> None:
        lessons = [{"id": "1", "content": "same content"}]
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            count1 = await _ingest.ingest_lessons(_make_config(), lessons)
            assert count1 == 1
            mock_cognee.add.reset_mock()
            mock_cognee.cognify.reset_mock()
            count2 = await _ingest.ingest_lessons(_make_config(), lessons)
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

        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_from_pool(_make_config(), mock_pool)
        assert count == 1
        mock_conn.fetch.assert_awaited_once()
