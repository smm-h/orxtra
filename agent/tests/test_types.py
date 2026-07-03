from __future__ import annotations

from decimal import Decimal

import pytest
from orxtra.agent import Agent, InlineToolDefinition
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


class TestInlineToolDefinition:
    def test_valid(self) -> None:
        itd = InlineToolDefinition(
            name="pytest",
            description="Run pytest",
            namespace="custom.exec",
            deferred=False,
            execution={"type": "command", "executable": "pytest",
                        "arg_validation": True, "timeout_ceiling": 300},
        )
        assert itd.name == "pytest"
        assert itd.namespace == "custom.exec"

    def test_rejects_extra(self) -> None:
        with pytest.raises(ValidationError):
            InlineToolDefinition(
                name="pytest",
                description="Run pytest",
                namespace="custom.exec",
                deferred=False,
                execution={"type": "command", "executable": "pytest",
                            "arg_validation": True, "timeout_ceiling": 300},
                extra="bad",  # type: ignore[call-arg]
            )


class TestAgentRouting:
    """Tests for the category vs provider/model validation."""

    def test_category_only(self) -> None:
        agent = Agent(
            name="a",
            description="d",
            prompt="p",
            category="fast",
            allow=[],
        )
        assert agent.category == "fast"
        assert agent.provider is None
        assert agent.model is None

    def test_provider_and_model_only(self) -> None:
        agent = Agent(
            name="a",
            description="d",
            prompt="p",
            provider="anthropic",
            model="claude-sonnet-4-6",
            allow=[],
        )
        assert agent.provider == "anthropic"
        assert agent.model == "claude-sonnet-4-6"
        assert agent.category is None

    def test_both_category_and_provider_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot have both"):
            Agent(
                name="a",
                description="d",
                prompt="p",
                category="fast",
                provider="anthropic",
                model="claude-sonnet-4-6",
                allow=[],
            )

    def test_category_and_provider_without_model_rejected(self) -> None:
        with pytest.raises(ValidationError, match="cannot have both"):
            Agent(
                name="a",
                description="d",
                prompt="p",
                category="fast",
                provider="anthropic",
                allow=[],
            )

    def test_provider_without_model_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must both be set"):
            Agent(
                name="a",
                description="d",
                prompt="p",
                provider="anthropic",
                allow=[],
            )

    def test_model_without_provider_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must both be set"):
            Agent(
                name="a",
                description="d",
                prompt="p",
                model="claude-sonnet-4-6",
                allow=[],
            )

    def test_neither_category_nor_provider_rejected(self) -> None:
        with pytest.raises(ValidationError, match="must have either"):
            Agent(
                name="a",
                description="d",
                prompt="p",
                allow=[],
            )


class TestAgentWithInlineTools:
    def test_agent_with_inline_tools(self) -> None:
        agent = Agent(
            name="builder",
            description="Builds",
            prompt="Build things",
            category="fast",
            allow=["read", "custom.*"],
            inline_tools=[
                InlineToolDefinition(
                    name="pytest",
                    description="Run tests",
                    namespace="custom.exec",
                    deferred=False,
                    execution={
                        "type": "command",
                        "executable": "pytest",
                        "arg_validation": True,
                        "timeout_ceiling": 120,
                    },
                ),
            ],
        )
        assert len(agent.inline_tools) == 1
        assert agent.inline_tools[0].name == "pytest"

    def test_agent_defaults_no_inline_tools(self) -> None:
        agent = Agent(
            name="basic",
            description="Basic",
            prompt="Do things",
            category="fast",
            allow=[],
        )
        assert agent.inline_tools == []


class TestAgentDefaults:
    """Tests for budget, write_paths, timeout defaults."""

    def test_agent_with_budget(self) -> None:
        agent = Agent(
            name="budgeted",
            description="Has budget",
            prompt="Work",
            category="fast",
            allow=[],
            budget=Decimal("5.00"),
        )
        assert agent.budget == Decimal("5.00")

    def test_agent_without_budget_defaults_none(self) -> None:
        agent = Agent(
            name="basic",
            description="No budget",
            prompt="Work",
            category="fast",
            allow=[],
        )
        assert agent.budget is None

    def test_agent_with_write_paths(self) -> None:
        agent = Agent(
            name="writer",
            description="Has paths",
            prompt="Work",
            category="fast",
            allow=["write"],
            write_paths=["src/", "tests/"],
        )
        assert agent.write_paths == ["src/", "tests/"]

    def test_agent_without_write_paths_defaults_none(self) -> None:
        agent = Agent(
            name="basic",
            description="No paths",
            prompt="Work",
            category="fast",
            allow=[],
        )
        assert agent.write_paths is None

    def test_agent_with_timeout(self) -> None:
        agent = Agent(
            name="timed",
            description="Has timeout",
            prompt="Work",
            category="fast",
            allow=[],
            timeout=300,
        )
        assert agent.timeout == 300

    def test_agent_without_timeout_defaults_none(self) -> None:
        agent = Agent(
            name="basic",
            description="No timeout",
            prompt="Work",
            category="fast",
            allow=[],
        )
        assert agent.timeout is None

    def test_all_defaults_together(self) -> None:
        agent = Agent(
            name="full",
            description="All defaults set",
            prompt="Work",
            category="fast",
            allow=["read", "write"],
            budget=Decimal("10.00"),
            write_paths=["src/"],
            timeout=600,
        )
        assert agent.budget == Decimal("10.00")
        assert agent.write_paths == ["src/"]
        assert agent.timeout == 600
