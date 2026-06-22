from __future__ import annotations

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


def _make_pool() -> MagicMock:
    pool = MagicMock()
    pool.fetchval = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


_COGNEE_MISSING = {"cognee": None}
_ERROR_MATCH = "cognee is required for the knowledge module"


@pytest.fixture(autouse=True)
def _reset_ingest_cache() -> None:
    from orxtra.knowledge_module import _ingest  # noqa: PLC0415

    _ingest._cache = None  # noqa: SLF001


class TestCogneeMissing:
    def test_configure_cognee_without_cognee_raises(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxtra.knowledge_module._config import configure_cognee  # noqa: PLC0415

            with pytest.raises(RuntimeError, match=_ERROR_MATCH):
                configure_cognee(_make_config())

    @pytest.mark.asyncio
    async def test_ingest_without_cognee_raises(self) -> None:
        pool = _make_pool()
        lessons = [{"id": "1", "content": "lesson one"}]
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            with pytest.raises(RuntimeError, match=_ERROR_MATCH):
                await _ingest.ingest_lessons(_make_config(), lessons, pool)

    @pytest.mark.asyncio
    async def test_ingest_empty_without_cognee_returns_zero(self) -> None:
        pool = _make_pool()
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxtra.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_lessons(_make_config(), [], pool)
        assert count == 0

    @pytest.mark.asyncio
    async def test_retrieve_without_cognee_raises(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxtra.knowledge_module import _retrieve  # noqa: PLC0415

            with pytest.raises(RuntimeError, match=_ERROR_MATCH):
                await _retrieve.retrieve_knowledge(
                    config=_make_config(), query="test",
                )

    @pytest.mark.asyncio
    async def test_retrieve_none_config_without_cognee_returns_empty(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxtra.knowledge_module._retrieve import (  # noqa: PLC0415
                retrieve_knowledge,
            )

            result = await retrieve_knowledge(config=None, query="test")
        assert result == []
