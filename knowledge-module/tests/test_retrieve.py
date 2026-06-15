from __future__ import annotations

import sys
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from orxt.knowledge_module._types import KnowledgeConfig, KnowledgeResult


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
    mock.search = AsyncMock(return_value=[])
    mock.config = MagicMock()
    mock.config.set_llm_config = MagicMock()
    mock.config.set_vector_db_config = MagicMock()
    return mock


class TestRetrieveKnowledge:
    @pytest.mark.asyncio
    async def test_none_config_returns_empty(self) -> None:
        from orxt.knowledge_module._retrieve import retrieve_knowledge

        result = await retrieve_knowledge(config=None, query="test")
        assert result == []

    @pytest.mark.asyncio
    async def test_returns_knowledge_results(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(
            return_value=[
                {"text": "lesson 1", "source": "db", "permanent": True, "score": 0.9},
                {"text": "lesson 2", "source": "db", "permanent": False, "score": 0.7},
            ]
        )
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _retrieve

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(), query="test query",
            )
        assert len(result) == 2
        assert isinstance(result[0], KnowledgeResult)
        assert result[0].text == "lesson 1"
        assert result[0].relevance_score == 0.9

    @pytest.mark.asyncio
    async def test_no_results_returns_empty(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(return_value=[])
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _retrieve

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(), query="nothing",
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_tags_param_accepted(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(return_value=[])
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _retrieve

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(), query="test", tags=["error-handling"],
            )
        assert result == []

    @pytest.mark.asyncio
    async def test_max_results_respected(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(
            return_value=[
                {"text": f"item {i}", "source": "db", "permanent": False, "score": 0.5}
                for i in range(20)
            ]
        )
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _retrieve

            result = await _retrieve.retrieve_knowledge(
                config=_make_config(), query="test", max_results=3,
            )
        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_cognee_error_propagated(self, mock_cognee: MagicMock) -> None:
        mock_cognee.search = AsyncMock(side_effect=RuntimeError("cognee down"))
        with patch.dict(sys.modules, {"cognee": mock_cognee}):
            from orxt.knowledge_module import _retrieve

            with pytest.raises(RuntimeError, match="cognee down"):
                await _retrieve.retrieve_knowledge(
                    config=_make_config(), query="test",
                )
