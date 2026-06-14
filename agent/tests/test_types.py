from __future__ import annotations

import pytest
from orxt.agent import Agent
from pydantic import ValidationError


class TestAgentModel:
    def test_valid_data(self) -> None:
        agent = Agent(
            name="coder",
            description="Writes code",
            prompt="Do coding",
            category="fast",
            allow=["read", "write"],
        )
        assert agent.name == "coder"
        assert agent.description == "Writes code"
        assert agent.prompt == "Do coding"
        assert agent.category == "fast"
        assert agent.allow == ["read", "write"]

    def test_rejects_extra_fields(self) -> None:
        with pytest.raises(ValidationError):
            Agent(
                name="coder",
                description="Writes code",
                prompt="Do coding",
                category="fast",
                allow=["read"],
                unknown_field="bad",  # type: ignore[call-arg]
            )

    def test_rejects_missing_required_fields(self) -> None:
        with pytest.raises(ValidationError):
            Agent(name="coder")  # type: ignore[call-arg]

    def test_rejects_wrong_types(self) -> None:
        with pytest.raises(ValidationError):
            Agent(
                name="coder",
                description="Writes code",
                prompt="Do coding",
                category="fast",
                allow="not_a_list",  # type: ignore[arg-type]
            )

    def test_frozen(self) -> None:
        agent = Agent(
            name="coder",
            description="Writes code",
            prompt="Do coding",
            category="fast",
            allow=[],
        )
        with pytest.raises(ValidationError):
            agent.name = "other"  # type: ignore[misc]
