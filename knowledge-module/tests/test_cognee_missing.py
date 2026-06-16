from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from orxt.knowledge_module._types import KnowledgeConfig


def _make_config() -> KnowledgeConfig:
    return KnowledgeConfig(
        db_url="postgresql://localhost/test",
        cognify_model="gpt-4o-mini",
        cognify_api_key="sk-test-key",
        max_retrieval_results=10,
    )


_COGNEE_MISSING = {"cognee": None}
_ERROR_MATCH = "cognee is required for the knowledge module"


class TestCogneeMissing:
    def test_configure_cognee_without_cognee_raises(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxt.knowledge_module._config import configure_cognee  # noqa: PLC0415

            with pytest.raises(RuntimeError, match=_ERROR_MATCH):
                configure_cognee(_make_config())

    @pytest.mark.asyncio
    async def test_ingest_without_cognee_raises(self) -> None:
        lessons = [{"id": "1", "content": "lesson one"}]
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            with pytest.raises(RuntimeError, match=_ERROR_MATCH):
                await _ingest.ingest_lessons(_make_config(), lessons)

    @pytest.mark.asyncio
    async def test_ingest_empty_without_cognee_returns_zero(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxt.knowledge_module import _ingest  # noqa: PLC0415

            count = await _ingest.ingest_lessons(_make_config(), [])
        assert count == 0

    @pytest.mark.asyncio
    async def test_retrieve_without_cognee_raises(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxt.knowledge_module import _retrieve  # noqa: PLC0415

            with pytest.raises(RuntimeError, match=_ERROR_MATCH):
                await _retrieve.retrieve_knowledge(
                    config=_make_config(), query="test",
                )

    @pytest.mark.asyncio
    async def test_retrieve_none_config_without_cognee_returns_empty(self) -> None:
        with patch.dict(sys.modules, _COGNEE_MISSING):
            from orxt.knowledge_module._retrieve import (  # noqa: PLC0415
                retrieve_knowledge,
            )

            result = await retrieve_knowledge(config=None, query="test")
        assert result == []
