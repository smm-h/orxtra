from __future__ import annotations

from decimal import Decimal

import pytest
from orxtra.agent import Agent, ExecToolConfig, ShellConfig
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


class TestExecToolConfig:
    def test_valid(self) -> None:
        cfg = ExecToolConfig(
            name="pytest",
            executable="pytest",
            description="Run pytest",
        )
        assert cfg.name == "pytest"
        assert cfg.timeout_ceiling == 300

    def test_custom_timeout(self) -> None:
        cfg = ExecToolConfig(
            name="pytest",
            executable="pytest",
            description="Run pytest",
            timeout_ceiling=60,
        )
        assert cfg.timeout_ceiling == 60

    def test_rejects_extra(self) -> None:
        with pytest.raises(ValidationError):
            ExecToolConfig(
                name="pytest",
                executable="pytest",
                description="Run pytest",
                extra="bad",
            )


class TestShellConfig:
    def test_valid(self) -> None:
        cfg = ShellConfig(allowed_binaries=["ls", "cat"])
        assert cfg.allowed_binaries == ["ls", "cat"]
        assert cfg.timeout_ceiling == 300

    def test_custom_description(self) -> None:
        cfg = ShellConfig(
            allowed_binaries=["ls"],
            description="Limited shell",
        )
        assert cfg.description == "Limited shell"


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


class TestAgentWithExecShell:
    def test_agent_with_exec_tools(self) -> None:
        agent = Agent(
            name="builder",
            description="Builds",
            prompt="Build things",
            category="fast",
            allow=["read", "exec"],
            exec_tools=[
                ExecToolConfig(
                    name="pytest",
                    executable="pytest",
                    description="Run tests",
                ),
            ],
        )
        assert len(agent.exec_tools) == 1
        assert agent.exec_tools[0].name == "pytest"

    def test_agent_with_shell(self) -> None:
        agent = Agent(
            name="builder",
            description="Builds",
            prompt="Build things",
            category="fast",
            allow=["read", "shell"],
            shell_config=ShellConfig(
                allowed_binaries=["ls", "cat"],
            ),
        )
        assert agent.shell_config is not None
        assert agent.shell_config.allowed_binaries == ["ls", "cat"]

    def test_agent_defaults_no_exec_no_shell(self) -> None:
        agent = Agent(
            name="basic",
            description="Basic",
            prompt="Do things",
            category="fast",
            allow=[],
        )
        assert agent.exec_tools == []
        assert agent.shell_config is None


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
