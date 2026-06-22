from __future__ import annotations

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
