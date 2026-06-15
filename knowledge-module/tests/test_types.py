from __future__ import annotations

import pytest
from orxt.knowledge_module._types import KnowledgeConfig, KnowledgeResult
from pydantic import ValidationError


def _make_config(**overrides: object) -> KnowledgeConfig:
    defaults = {
        "db_url": "postgresql://localhost/test",
        "cognify_model": "gpt-4o-mini",
        "cognify_api_key": "sk-test-key",
        "max_retrieval_results": 10,
    }
    defaults.update(overrides)
    return KnowledgeConfig(**defaults)


class TestKnowledgeConfig:
    def test_valid_config(self) -> None:
        config = _make_config()
        assert config.db_url == "postgresql://localhost/test"
        assert config.cognify_model == "gpt-4o-mini"
        assert config.cognify_api_key == "sk-test-key"
        assert config.max_retrieval_results == 10

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            _make_config(unknown_field="value")

    def test_rejects_missing_fields(self) -> None:
        with pytest.raises(ValidationError):
            KnowledgeConfig(db_url="postgresql://localhost/test")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        config = _make_config()
        with pytest.raises(ValidationError):
            config.db_url = "other"  # type: ignore[misc]

    def test_strict_rejects_wrong_types(self) -> None:
        with pytest.raises(ValidationError):
            _make_config(max_retrieval_results="not_an_int")


class TestKnowledgeResult:
    def test_fields_accessible(self) -> None:
        result = KnowledgeResult(
            text="hello", source="test", permanent=True, relevance_score=0.95,
        )
        assert result.text == "hello"
        assert result.source == "test"
        assert result.permanent is True
        assert result.relevance_score == 0.95

    def test_frozen(self) -> None:
        result = KnowledgeResult(
            text="hello", source="test", permanent=False, relevance_score=0.5,
        )
        with pytest.raises(AttributeError):
            result.text = "other"  # type: ignore[misc]
