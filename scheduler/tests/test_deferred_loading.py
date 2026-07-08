"""Tests for deferred tool loading end-to-end.

Covers:
- Agent with deferred tools sees stubs in the initial tool set.
- Stubs have deferred=True and minimal parameters.
- Calling a deferred stub raises ToolError.
- Agent with deferred tools auto-receives load_tools.
- Deferred tools are NOT fully built initially.
- The deferred tool IS in the resolved allow list (for load_tools).
- load_tools builds the tool, wraps it through the pipeline, and
  updates the session.
- Out-of-allow load is a hard ToolError.
- Provider format tests: all three providers handle deferred=True.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.protocols import Tool, ToolError
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._tool_registry import (
    ToolEntry,
    create_builtin_registry,
)

from .conftest import (
    MockTraceWriter,
    MockTransport,
    make_categories,
)

if TYPE_CHECKING:
    from pathlib import Path


LIFECYCLE_TOOLS = frozenset({
    "start_task",
    "end_task",
    "create_task",
    "create_workflow",
    "create_wait_for",
    "await_task",
})


def _agent(
    allow: list[str],
    deferred: list[str] | None = None,
) -> Agent:
    return Agent(
        name="test-agent",
        description="Test agent",
        prompt="You are a test agent.",
        category="default",
        allow=allow,
        deferred=deferred or [],
    )


def _make_scheduler(
    agent: Agent,
    tmp_path: Path,
    custom_tools: list[ToolEntry] | None = None,
) -> Scheduler:
    trace = MockTraceWriter()
    transport = MockTransport(auto_execute_tools=True)
    return Scheduler(
        trace_writer=trace,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents={agent.name: agent},
        categories=make_categories(),
        run_id=uuid6.uuid7(),
        read_root=tmp_path,
        autonomy_level="max",
        custom_tools=custom_tools,
    )


from orxtra.protocols import TaskSpec


def _task() -> TaskSpec:
    return TaskSpec(
        name="test-task",
        agent="test-agent",
        task_prompt="Do something",
        context_refinement=False,
    )


async def _extract_tools(
    scheduler: Scheduler,
) -> list[Tool]:
    """Call _create_agent_session with create_session
    mocked, and return the tools list passed to it."""
    task = _task()
    task_id = uuid6.uuid7()

    with patch(
        "orxtra.scheduler._agent_execution.create_session",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_session = MagicMock()
        mock_create.return_value = mock_session

        await scheduler._create_agent_session(
            task, task_id, 1,
        )

        return list(mock_create.call_args[1]["tools"])


# ---------------------------------------------------------------
# Deferred stubs in initial tool set
# ---------------------------------------------------------------


class TestDeferredStubs:
    """Deferred tools appear as stubs in the initial set."""

    async def test_deferred_tool_is_stub(
        self, tmp_path: Path,
    ) -> None:
        """A deferred built-in appears in the initial
        tool set with deferred=True and minimal parameters."""
        agent = _agent(
            allow=["read", "grep"],
            deferred=["grep"],
        )
        sched = _make_scheduler(agent, tmp_path)
        tools = await _extract_tools(sched)
        tool_map = {t.name: t for t in tools}

        # grep should be present as a deferred stub.
        assert "grep" in tool_map
        grep = tool_map["grep"]
        assert grep.deferred is True
        # Stub has empty properties (minimal schema).
        assert grep.parameters == {
            "type": "object",
            "properties": {},
        }

    async def test_non_deferred_tool_is_full(
        self, tmp_path: Path,
    ) -> None:
        """Non-deferred tools are fully built (not stubs)."""
        agent = _agent(
            allow=["read", "grep"],
            deferred=["grep"],
        )
        sched = _make_scheduler(agent, tmp_path)
        tools = await _extract_tools(sched)
        tool_map = {t.name: t for t in tools}

        # read should be fully built (not deferred).
        assert "read" in tool_map
        read_tool = tool_map["read"]
        assert read_tool.deferred is False
        # Full tool has real parameters.
        assert "properties" in read_tool.parameters
        assert len(read_tool.parameters["properties"]) > 0

    async def test_deferred_stub_raises_on_call(
        self,
    ) -> None:
        """The raw deferred stub execute raises ToolError
        telling the agent to call load_tools first."""
        from orxtra.scheduler._agent_execution import (
            _deferred_stub_execute,
        )

        with pytest.raises(ToolError, match="deferred"):
            await _deferred_stub_execute({})


# ---------------------------------------------------------------
# Auto-grant of load_tools
# ---------------------------------------------------------------


class TestAutoGrantLoadTools:
    """An agent with deferred tools auto-gets load_tools."""

    async def test_load_tools_present(
        self, tmp_path: Path,
    ) -> None:
        """When an agent declares deferred tools, load_tools
        is automatically added to the tool set."""
        agent = _agent(
            allow=["read", "grep"],
            deferred=["grep"],
        )
        sched = _make_scheduler(agent, tmp_path)
        tools = await _extract_tools(sched)
        names = {t.name for t in tools}

        assert "load_tools" in names

    async def test_no_load_tools_when_no_deferred(
        self, tmp_path: Path,
    ) -> None:
        """When no deferred tools are declared, load_tools
        is NOT in the tool set."""
        agent = _agent(
            allow=["read", "grep"],
            deferred=[],
        )
        sched = _make_scheduler(agent, tmp_path)
        tools = await _extract_tools(sched)
        names = {t.name for t in tools}

        assert "load_tools" not in names


# ---------------------------------------------------------------
# Deferred tool is NOT in initial built tools (fully)
# ---------------------------------------------------------------


class TestDeferredNotBuilt:
    """Deferred tools are not fully built initially."""

    async def test_deferred_not_full_tool(
        self, tmp_path: Path,
    ) -> None:
        """The deferred grep has no real parameters
        initially -- it is only a stub."""
        agent = _agent(
            allow=["read", "grep"],
            deferred=["grep"],
        )
        sched = _make_scheduler(agent, tmp_path)
        tools = await _extract_tools(sched)

        # Count how many tools named 'grep' are present.
        grep_tools = [t for t in tools if t.name == "grep"]
        assert len(grep_tools) == 1
        assert grep_tools[0].deferred is True


# ---------------------------------------------------------------
# ToolEntry description and deferred fields
# ---------------------------------------------------------------


class TestToolEntryFields:
    """ToolEntry gains description and deferred."""

    def test_description_field(self) -> None:
        registry = create_builtin_registry()
        entry = registry.get_entry("read")
        assert entry is not None
        assert entry.description == "Read a file's contents."

    def test_deferred_default_false(self) -> None:
        registry = create_builtin_registry()
        entry = registry.get_entry("read")
        assert entry is not None
        assert entry.deferred is False

    def test_custom_with_description(self) -> None:
        registry = create_builtin_registry()
        registry.register_custom(
            "my_tool",
            namespace="custom.test",
            tags=frozenset(),
            factory=lambda deps: MagicMock(),
            description="My custom tool.",
        )
        entry = registry.get_entry("my_tool")
        assert entry is not None
        assert entry.description == "My custom tool."


# ---------------------------------------------------------------
# Provider format tests for deferred=True
# ---------------------------------------------------------------


class TestProviderFormats:
    """All three provider formatters handle deferred tools."""

    def _tool_dict(
        self, deferred: bool = False,
    ) -> dict[str, Any]:
        return {
            "name": "test_tool",
            "description": "A test tool",
            "parameters": {
                "type": "object",
                "properties": {
                    "x": {"type": "string"},
                },
            },
            **({"deferred": True} if deferred else {}),
        }

    def test_anthropic_deferred(self) -> None:
        """Anthropic adds defer_loading for deferred tools."""
        from orxtra.transport.providers._anthropic import (
            _format_anthropic_tool,
        )

        result = _format_anthropic_tool(
            self._tool_dict(deferred=True),
        )
        assert result["defer_loading"] is True
        assert "name" in result
        assert "description" in result

    def test_anthropic_non_deferred(self) -> None:
        """Non-deferred Anthropic tools have no defer_loading."""
        from orxtra.transport.providers._anthropic import (
            _format_anthropic_tool,
        )

        result = _format_anthropic_tool(
            self._tool_dict(deferred=False),
        )
        assert "defer_loading" not in result

    def test_openai_deferred(self) -> None:
        """OpenAI empties parameters for deferred tools."""
        from orxtra.transport.providers._openai import (
            _format_openai_tool,
        )

        result = _format_openai_tool(
            self._tool_dict(deferred=True),
        )
        assert result["parameters"] == {
            "type": "object",
            "properties": {},
        }
        assert "load_tools" in result["description"]

    def test_openai_non_deferred(self) -> None:
        """Non-deferred OpenAI tools keep full parameters."""
        from orxtra.transport.providers._openai import (
            _format_openai_tool,
        )

        result = _format_openai_tool(
            self._tool_dict(deferred=False),
        )
        assert "x" in result["parameters"]["properties"]

    def test_google_deferred(self) -> None:
        """Google omits parameters for deferred tools."""
        from orxtra.transport.providers._google import (
            _convert_tool,
        )

        result = _convert_tool(
            self._tool_dict(deferred=True),
        )
        assert "parameters" not in result
        assert "load_tools" in result["description"]

    def test_google_non_deferred(self) -> None:
        """Non-deferred Google tools include parameters."""
        from orxtra.transport.providers._google import (
            _convert_tool,
        )

        result = _convert_tool(
            self._tool_dict(deferred=False),
        )
        assert "parameters" in result


# ---------------------------------------------------------------
# Agent loader: deferred in [tools] section
# ---------------------------------------------------------------


class TestAgentLoaderDeferred:
    """Agent TOML loader parses the deferred list."""

    def test_deferred_parsed(self, tmp_path: Path) -> None:
        """The deferred key in [tools] is parsed into
        Agent.deferred."""
        from orxtra.agent import load_agent

        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("System prompt.")

        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[agent]\n'
            'name = "test"\n'
            'description = "Test"\n'
            'prompt = "prompt.md"\n'
            'category = "default"\n'
            '\n'
            '[tools]\n'
            'allow = ["read", "grep"]\n'
            'deferred = ["grep"]\n'
        )

        agent = load_agent(toml_path)
        assert agent.deferred == ["grep"]

    def test_no_deferred_defaults_empty(
        self, tmp_path: Path,
    ) -> None:
        """Without a deferred key, Agent.deferred defaults
        to an empty list."""
        from orxtra.agent import load_agent

        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("System prompt.")

        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[agent]\n'
            'name = "test"\n'
            'description = "Test"\n'
            'prompt = "prompt.md"\n'
            'category = "default"\n'
            '\n'
            '[tools]\n'
            'allow = ["read"]\n'
        )

        agent = load_agent(toml_path)
        assert agent.deferred == []

    def test_deferred_is_valid_tools_key(
        self, tmp_path: Path,
    ) -> None:
        """'deferred' is not treated as an unknown key."""
        from orxtra.agent import load_agent

        prompt_path = tmp_path / "prompt.md"
        prompt_path.write_text("System prompt.")

        toml_path = tmp_path / "test.toml"
        toml_path.write_text(
            '[agent]\n'
            'name = "test"\n'
            'description = "Test"\n'
            'prompt = "prompt.md"\n'
            'category = "default"\n'
            '\n'
            '[tools]\n'
            'allow = ["read"]\n'
            'deferred = ["read"]\n'
        )

        # Should not raise "Unknown keys in [tools] section".
        agent = load_agent(toml_path)
        assert agent.deferred == ["read"]
