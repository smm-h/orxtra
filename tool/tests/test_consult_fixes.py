"""Tests verifying each of the four consult tool bug fixes.

Each test class targets one specific fix:
1. Uses agent_def.allow (not .tools)
2. Iterates async generator event stream correctly
3. Passes system_prompt to transport.send
4. Git in consult mode has read-only subcommands only
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
from orxt.protocols._tool import Tool, ToolError
from orxt.tool._consult_tool import make_consult_tool

_RUN_ID = UUID("12345678-1234-1234-1234-123456789abc")
_READ_ROOT = Path("/project")
_CATEGORIES = {"default": "anthropic/claude-3-sonnet"}


def _dummy_tool(name: str) -> Tool:
    """Create a no-op tool for registry population."""

    async def _noop(args: dict[str, Any]) -> str:
        return "ok"

    return Tool(name=name, description=f"Tool {name}", parameters={}, execute=_noop)


@dataclass(frozen=True)
class Result:
    """Mirrors transport Result for testing without the dependency.

    Name must be exactly 'Result' because the consult code checks
    type(event).__name__ == "Result".
    """

    text: str
    session_id: str = "mock"


class _MockTransport:
    """Mock transport whose send is an async generator."""

    def __init__(self, events: list[Any] | None = None) -> None:
        self._events = [Result(text="answer")] if events is None else events
        self.send_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def send(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        self.send_calls.append((args, kwargs))
        for event in self._events:
            yield event


def _full_registry() -> dict[str, Tool]:
    """Build a registry with all tool names from the codebase."""
    names = [
        "write", "edit", "delete", "move", "copy", "mkdir", "set_executable",
        "exec", "git", "http",
        "start_task", "end_task", "create_task", "create_workflow", "create_wait_for",
        "read", "notepad",
    ]
    return {name: _dummy_tool(name) for name in names}


def _build(
    agents: dict[str, Any] | None = None,
    events: list[Any] | None = None,
) -> tuple[Tool, _MockTransport]:
    """Build a consult tool with defaults, return (tool, mock_transport)."""
    transport = _MockTransport(events)
    if agents is None:
        agent = MagicMock()
        agent.category = "default"
        agent.allow = ["read"]
        agent.prompt = "You are helpful."
        agents = {"helper": agent}
    tool = make_consult_tool(
        tool_registry=_full_registry(),
        transport_registry={"anthropic": transport},
        trace_writer=MagicMock(),
        run_id=_RUN_ID,
        read_root=_READ_ROOT,
        categories=_CATEGORIES,
        agents=agents,
    )
    return tool, transport


class TestUsesAllowNotTools:
    """Fix 1: consult accesses agent_def.allow, not agent_def.tools."""

    @pytest.mark.asyncio
    async def test_agent_without_tools_attr_succeeds(self) -> None:
        """An agent mock with .allow but no .tools should not raise."""
        # spec limits the mock to exactly these attributes -- accessing
        # anything else (like .tools) raises AttributeError.
        agent = MagicMock(spec=["category", "allow", "prompt"])
        agent.category = "default"
        agent.allow = ["read"]
        agent.prompt = "You are helpful."

        tool, _ = _build(agents={"helper": agent})
        # If the code mistakenly accessed .tools, this would raise
        # AttributeError.  With the fix it accesses .allow and succeeds.
        result = await tool.execute({"agent": "helper", "question": "hi"})
        assert isinstance(result, str)

    @pytest.mark.asyncio
    async def test_allow_list_controls_http_reconstruction(self) -> None:
        """HTTP tool is reconstructed when 'http' is in allow, not tools."""
        agent = MagicMock(spec=["category", "allow", "prompt"])
        agent.category = "default"
        agent.allow = ["read", "http"]
        agent.prompt = "You are helpful."

        tool, transport = _build(agents={"web": agent})
        await tool.execute({"agent": "web", "question": "fetch"})

        _, call_kwargs = transport.send_calls[-1]
        sent_names = {t.name for t in call_kwargs["tools"]}
        assert "http" in sent_names


class TestEventStreamIteration:
    """Fix 2: consult iterates the async generator from transport.send."""

    @pytest.mark.asyncio
    async def test_extracts_result_from_multi_event_stream(self) -> None:
        """When the stream yields multiple events, Result text is extracted."""

        @dataclass(frozen=True)
        class Thinking:
            content: str

        events = [
            Thinking(content="let me think..."),
            Result(text="the answer"),
        ]
        tool, _ = _build(events=events)
        result = await tool.execute({"agent": "helper", "question": "q"})
        assert result == "the answer"

    @pytest.mark.asyncio
    async def test_last_result_wins(self) -> None:
        """If the stream has multiple Result events, the last one is kept."""
        events = [
            Result(text="first"),
            Result(text="second"),
        ]
        tool, _ = _build(events=events)
        result = await tool.execute({"agent": "helper", "question": "q"})
        assert result == "second"

    @pytest.mark.asyncio
    async def test_empty_stream_returns_empty_string(self) -> None:
        """A stream with no Result events returns empty string."""
        tool, _ = _build(events=[])
        result = await tool.execute({"agent": "helper", "question": "q"})
        assert result == ""


class TestSystemPromptPassed:
    """Fix 3: consult passes agent_def.prompt as system_prompt."""

    @pytest.mark.asyncio
    async def test_system_prompt_matches_agent_prompt(self) -> None:
        """transport.send receives system_prompt equal to agent_def.prompt."""
        agent = MagicMock()
        agent.category = "default"
        agent.allow = ["read"]
        agent.prompt = "You are an expert code reviewer."

        tool, transport = _build(agents={"reviewer": agent})
        await tool.execute({"agent": "reviewer", "question": "review this"})

        _, call_kwargs = transport.send_calls[-1]
        assert call_kwargs["system_prompt"] == "You are an expert code reviewer."

    @pytest.mark.asyncio
    async def test_system_prompt_kwarg_is_present(self) -> None:
        """system_prompt is always an explicit kwarg, never omitted."""
        tool, transport = _build()
        await tool.execute({"agent": "helper", "question": "q"})

        _, call_kwargs = transport.send_calls[-1]
        assert "system_prompt" in call_kwargs


class TestGitReadOnlySubcommands:
    """Fix 4: git tool in consult mode allows only read-only subcommands."""

    @pytest.mark.asyncio
    async def test_commit_rejected(self) -> None:
        """git commit is rejected in consult mode."""
        agent = MagicMock()
        agent.category = "default"
        agent.allow = ["read", "git"]
        agent.prompt = "You are helpful."

        tool, transport = _build(agents={"dev": agent})
        await tool.execute({"agent": "dev", "question": "show diff"})

        _, call_kwargs = transport.send_calls[-1]
        git_tools = [t for t in call_kwargs["tools"] if t.name == "git"]
        assert len(git_tools) == 1

        with pytest.raises(ToolError, match="not allowed"):
            await git_tools[0].execute(
                {"subcommand": "commit", "args": ["msg", "file.py"]},
            )

    @pytest.mark.asyncio
    async def test_read_only_subcommands_accepted(self) -> None:
        """Read-only subcommands like status and diff do not raise ToolError."""
        agent = MagicMock()
        agent.category = "default"
        agent.allow = ["read", "git"]
        agent.prompt = "You are helpful."

        tool, transport = _build(agents={"dev": agent})
        await tool.execute({"agent": "dev", "question": "status"})

        _, call_kwargs = transport.send_calls[-1]
        git_tools = [t for t in call_kwargs["tools"] if t.name == "git"]
        git_tool = git_tools[0]

        # These should NOT raise ToolError for "not allowed" -- they may
        # raise other errors (e.g., git not available, path issues) which
        # is fine; we only care they pass the subcommand allowlist check.
        read_only = ["status", "diff", "log", "show", "blame", "branches", "changed_files"]
        for subcmd in read_only:
            try:
                await git_tool.execute({"subcommand": subcmd})
            except ToolError as e:
                assert "not allowed" not in str(e), (
                    f"Read-only subcommand {subcmd!r} was rejected"
                )
            except Exception:  # noqa: BLE001
                # Other failures (subprocess, path, etc.) are fine
                pass
