from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.knowledge_module._types import KnowledgeConfig


def _make_config(max_retrieval_results: int = 10) -> KnowledgeConfig:
    return KnowledgeConfig(
        db_url="postgresql://localhost/test",
        cognify_model="gpt-4o-mini",
        cognify_api_key="sk-test-key",
        max_retrieval_results=max_retrieval_results,
    )


@pytest.fixture
def mock_cognee() -> MagicMock:
    mock = MagicMock()
    mock.search = AsyncMock(return_value=[])
    mock.config = MagicMock()
    mock.config.set_llm_config = MagicMock()
    mock.config.set_vector_db_config = MagicMock()
    return mock


class TestTagFiltering:
    @pytest.mark.asyncio
    async def test_tags_filter_results(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(
            return_value=[
                {
                    "text": "match", "source": "db",
                    "permanent": False, "score": 0.9,
                    "tags": ["tag1", "tag2"],
                },
                {
                    "text": "no match", "source": "db",
                    "permanent": False, "score": 0.8,
                    "tags": ["tag3"],
                },
                {
                    "text": "also match", "source": "db",
                    "permanent": False, "score": 0.7,
                    "tags": ["tag1"],
                },
            ]
        )
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _retrieve  # noqa: PLC0415

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(), query="test", tags=["tag1"],
            )
        assert len(result) == 2
        assert result[0].text == "match"
        assert result[1].text == "also match"

    @pytest.mark.asyncio
    async def test_max_results_from_config(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(
            return_value=[
                {"text": f"item {i}", "source": "db", "permanent": False, "score": 0.5}
                for i in range(10)
            ]
        )
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _retrieve  # noqa: PLC0415

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(max_retrieval_results=2), query="test",
            )
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_max_results_param_overrides_config(
        self, mock_cognee: MagicMock,
    ) -> None:
        mock_cognee.search = AsyncMock(
            return_value=[
                {"text": f"item {i}", "source": "db", "permanent": False, "score": 0.5}
                for i in range(10)
            ]
        )
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxtra.knowledge_module import _retrieve  # noqa: PLC0415

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(max_retrieval_results=10),
                query="test",
                max_results=1,
            )
        assert len(result) == 1
