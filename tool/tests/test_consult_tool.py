"""Tests for the consult tool constructor."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest
from orxt.protocols._tool import Tool, ToolError
from orxt.tool._consult_tool import CONSULT_STRIP_TOOLS, make_consult_tool

_RUN_ID = UUID("12345678-1234-1234-1234-123456789abc")
_READ_ROOT = Path("/project")
_CATEGORIES = {"default": "anthropic/claude-3-sonnet"}


def _dummy_tool(name: str) -> Tool:
    """Create a no-op tool for registry population."""

    async def _noop(args: dict[str, Any]) -> str:
        return "ok"

    return Tool(name=name, description=f"Tool {name}", parameters={}, execute=_noop)


def _mock_agent_def(
    category: str = "default", tools: list[str] | None = None,
) -> MagicMock:
    """Create a mock agent definition."""
    agent = MagicMock()
    agent.category = category
    agent.tools = tools or []
    return agent


def _mock_transport(text: str = "answer") -> MagicMock:
    """Create a mock transport that returns a canned response."""
    response = MagicMock()
    response.text = text
    transport = MagicMock()
    transport.send = AsyncMock(return_value=response)
    return transport


# All tool names that appear in the codebase.
_ALL_TOOL_NAMES: list[str] = [
    "write", "edit", "delete", "move", "copy", "mkdir", "set_executable",
    "exec", "git", "http",
    "start_task", "end_task", "create_task", "create_workflow", "create_wait_for",
    "read", "notepad",
]


def _full_registry() -> dict[str, Tool]:
    """Build a registry containing every tool name."""
    return {name: _dummy_tool(name) for name in _ALL_TOOL_NAMES}


def _make_tool(
    registry: dict[str, Tool] | None = None,
    agents: dict[str, Any] | None = None,
    transport_text: str = "answer",
) -> tuple[Tool, MagicMock]:
    """Build a consult tool with sensible defaults.

    Returns the consult tool and the mock transport for assertions.
    """
    if registry is None:
        registry = _full_registry()
    mock_transport = _mock_transport(transport_text)
    if agents is None:
        agents = {"helper": _mock_agent_def(tools=["read", "notepad"])}
    tool = make_consult_tool(
        tool_registry=registry,
        transport_registry={"anthropic": mock_transport},
        trace_writer=MagicMock(),
        run_id=_RUN_ID,
        read_root=_READ_ROOT,
        categories=_CATEGORIES,
        agents=agents,
    )
    return tool, mock_transport


def _sent_tool_names(transport: MagicMock) -> set[str]:
    """Extract tool names passed to transport.send()."""
    call_kwargs = transport.send.call_args[1]
    return {t.name for t in call_kwargs["tools"]}


class TestConsultToolMetadata:
    """Metadata-level tests for make_consult_tool."""

    def test_name(self) -> None:
        """Tool name is 'consult'."""
        tool, _ = _make_tool()
        assert tool.name == "consult"

    def test_parameters_schema(self) -> None:
        """Schema has required agent and question fields."""
        tool, _ = _make_tool()
        props = tool.parameters["properties"]
        assert "agent" in props
        assert "question" in props
        assert set(tool.parameters["required"]) == {"agent", "question"}


class TestConsultExecution:
    """Tests for basic consult execution."""

    @pytest.mark.asyncio
    async def test_valid_consult_returns_response(self) -> None:
        """Consult returns the transport's text response."""
        tool, _ = _make_tool(transport_text="The answer is 42")
        result = await tool.execute({"agent": "helper", "question": "What is 6*7?"})
        assert result == "The answer is 42"

    @pytest.mark.asyncio
    async def test_unknown_agent_raises_tool_error(self) -> None:
        """Referencing an unknown agent raises ToolError."""
        tool, _ = _make_tool()
        with pytest.raises(ToolError, match="Unknown agent"):
            await tool.execute({"agent": "nonexistent", "question": "hi"})

    @pytest.mark.asyncio
    async def test_transport_receives_question(self) -> None:
        """Transport.send is called with the question."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "Tell me about X"})
        call_args = transport.send.call_args[0]
        assert call_args[0] == "Tell me about X"

    @pytest.mark.asyncio
    async def test_transport_receives_model(self) -> None:
        """Transport.send is called with the parsed model name."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        call_kwargs = transport.send.call_args[1]
        assert call_kwargs["model"] == "claude-3-sonnet"


class TestConsultToolStripping:
    """Tests that mutating tools are stripped from consult sessions."""

    @pytest.mark.asyncio
    async def test_write_tool_stripped(self) -> None:
        """Write tool is not passed to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "write" not in _sent_tool_names(transport)

    @pytest.mark.asyncio
    async def test_edit_tool_stripped(self) -> None:
        """Edit tool is not passed to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "edit" not in _sent_tool_names(transport)

    @pytest.mark.asyncio
    async def test_delete_tool_stripped(self) -> None:
        """Delete tool is not passed to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "delete" not in _sent_tool_names(transport)

    @pytest.mark.asyncio
    async def test_exec_tool_stripped(self) -> None:
        """Exec tool is not passed to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "exec" not in _sent_tool_names(transport)

    @pytest.mark.asyncio
    async def test_git_tool_stripped(self) -> None:
        """Git tool is not passed to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "git" not in _sent_tool_names(transport)

    @pytest.mark.asyncio
    async def test_task_lifecycle_tools_stripped(self) -> None:
        """All task lifecycle tools are stripped."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        names = _sent_tool_names(transport)
        for lifecycle_tool in (
            "start_task", "end_task", "create_task",
            "create_workflow", "create_wait_for",
        ):
            assert lifecycle_tool not in names


class TestConsultToolPreservation:
    """Tests that read-only tools are preserved in consult sessions."""

    @pytest.mark.asyncio
    async def test_read_tool_preserved(self) -> None:
        """Read tool remains available to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "read" in _sent_tool_names(transport)

    @pytest.mark.asyncio
    async def test_notepad_tool_preserved(self) -> None:
        """Notepad tool remains available to the consulted agent."""
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "notepad" in _sent_tool_names(transport)


class TestConsultHttpReconstruction:
    """Tests for HTTP tool reconstruction in consult mode."""

    @pytest.mark.asyncio
    async def test_http_reconstructed_in_consult_mode(self) -> None:
        """HTTP tool is reconstructed with consult_mode=True when agent has http."""
        agents = {"web_reader": _mock_agent_def(tools=["read", "http"])}
        tool, transport = _make_tool(agents=agents)
        await tool.execute({"agent": "web_reader", "question": "fetch data"})
        names = _sent_tool_names(transport)
        assert "http" in names
        # Verify the reconstructed http tool has consult_mode schema
        call_kwargs = transport.send.call_args[1]
        http_tools = [t for t in call_kwargs["tools"] if t.name == "http"]
        assert len(http_tools) == 1
        method_enum = http_tools[0].parameters["properties"]["method"]["enum"]
        assert method_enum == ["GET", "HEAD"]

    @pytest.mark.asyncio
    async def test_http_not_reconstructed_when_agent_lacks_http(self) -> None:
        """HTTP tool stays stripped when agent doesn't have http in its tools."""
        # Default helper agent has tools=["read", "notepad"] -- no "http"
        tool, transport = _make_tool()
        await tool.execute({"agent": "helper", "question": "q"})
        assert "http" not in _sent_tool_names(transport)
