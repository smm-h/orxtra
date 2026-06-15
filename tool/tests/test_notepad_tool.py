from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from orxt.protocols._tool import ToolError
from orxt.tool._notepad_tool import make_notepad_tool


class TestMakeNotepadTool:
    def test_returns_tool_with_correct_name(self) -> None:
        trace_writer = MagicMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        assert tool.name == "notepad"

    def test_returns_tool_with_parameters_schema(self) -> None:
        trace_writer = MagicMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        props = tool.parameters["properties"]
        assert "type" in props
        assert "text" in props
        assert set(tool.parameters["required"]) == {"type", "text"}


class TestNotepadExecution:
    @pytest.mark.asyncio
    async def test_learning_entry_calls_trace_writer(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        await tool.execute({"type": "learning", "text": "Found a pattern"})
        trace_writer.write_notepad_entry.assert_awaited_once_with(
            "run-1", "task-1", "agent-1", "learning", "Found a pattern"
        )

    @pytest.mark.asyncio
    async def test_decision_entry_calls_trace_writer(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        await tool.execute({"type": "decision", "text": "Chose approach A"})
        trace_writer.write_notepad_entry.assert_awaited_once_with(
            "run-1", "task-1", "agent-1", "decision", "Chose approach A"
        )

    @pytest.mark.asyncio
    async def test_issue_entry_calls_trace_writer(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        await tool.execute({"type": "issue", "text": "Database timeout"})
        trace_writer.write_notepad_entry.assert_awaited_once_with(
            "run-1", "task-1", "agent-1", "issue", "Database timeout"
        )

    @pytest.mark.asyncio
    async def test_returns_confirmation_string(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        result = await tool.execute({"type": "learning", "text": "Something"})
        assert result == "Notepad entry recorded (type=learning)."

    @pytest.mark.asyncio
    async def test_all_params_passed_through_correctly(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(
            trace_writer, "run-xyz-99", "deploy-stage", "verifier-agent"
        )
        await tool.execute({"type": "decision", "text": "Go with plan B"})
        trace_writer.write_notepad_entry.assert_awaited_once_with(
            "run-xyz-99", "deploy-stage", "verifier-agent", "decision", "Go with plan B"
        )


class TestNotepadValidation:
    @pytest.mark.asyncio
    async def test_invalid_type_raises_tool_error(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        with pytest.raises(ToolError):
            await tool.execute({"type": "warning", "text": "Something"})

    @pytest.mark.asyncio
    async def test_missing_type_raises_tool_error(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        with pytest.raises(ToolError):
            await tool.execute({"text": "Something"})

    @pytest.mark.asyncio
    async def test_missing_text_raises_tool_error(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        with pytest.raises(ToolError):
            await tool.execute({"type": "learning"})

    @pytest.mark.asyncio
    async def test_empty_text_raises_tool_error(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        with pytest.raises(ToolError):
            await tool.execute({"type": "learning", "text": ""})

    @pytest.mark.asyncio
    async def test_extra_field_raises_tool_error(self) -> None:
        trace_writer = MagicMock()
        trace_writer.write_notepad_entry = AsyncMock()
        tool = make_notepad_tool(trace_writer, "run-1", "task-1", "agent-1")
        with pytest.raises(ToolError):
            await tool.execute({"type": "learning", "text": "X", "extra": "bad"})
