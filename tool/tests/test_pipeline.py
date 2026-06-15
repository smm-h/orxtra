"""Tests for the tool execution pipeline wrapper."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from orxt.protocols._tool import Tool, ToolError
from orxt.secrets._registry import SecretRegistry
from orxt.tool._pipeline import wrap_tool_with_pipeline, wrap_tools_for_session

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "session-1"
_TASK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _dummy_tool(name: str = "test_tool", result: str = "ok") -> Tool:
    """Create a simple async tool for testing."""

    async def _execute(args: dict[str, Any]) -> str:
        return result

    return Tool(
        name=name,
        description="Test tool",
        parameters={"type": "object"},
        execute=_execute,
    )


def _passing_scheduler_check(session_id: str) -> UUID:
    """Scheduler check that always succeeds."""
    return _TASK_ID


def _failing_scheduler_check(session_id: str) -> UUID:
    """Scheduler check that always fails."""
    raise ToolError("No active task for session")


# ---------------------------------------------------------------------------
# TestActiveTaskCheck
# ---------------------------------------------------------------------------


class TestActiveTaskCheck:
    """Tests for active task enforcement."""

    @pytest.mark.asyncio
    async def test_no_active_task_raises_tool_error(self) -> None:
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(),
            scheduler_check=_failing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        with pytest.raises(ToolError, match="No active task for session"):
            await wrapped.execute({})

    @pytest.mark.asyncio
    async def test_start_task_exempt_from_check(self) -> None:
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(),
            scheduler_check=_failing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            is_start_task=True,
        )
        result = await wrapped.execute({})
        assert result == "ok"

    @pytest.mark.asyncio
    async def test_active_task_present_executes_normally(self) -> None:
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(result="success"),
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        assert result == "success"


# ---------------------------------------------------------------------------
# TestSecretSubstitution
# ---------------------------------------------------------------------------


class TestSecretSubstitution:
    """Tests for secret substitution and scrubbing."""

    @pytest.mark.asyncio
    async def test_secrets_substituted_in_args(self) -> None:
        registry = SecretRegistry({"API_KEY": "real-key-123"})
        captured_args: dict[str, Any] = {}

        async def _capture(args: dict[str, Any]) -> str:
            captured_args.update(args)
            return "ok"

        tool = Tool(
            name="test",
            description="t",
            parameters={"type": "object"},
            execute=_capture,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        await wrapped.execute({"url": "{{secret:API_KEY}}"})
        assert captured_args["url"] == "real-key-123"

    @pytest.mark.asyncio
    async def test_secrets_scrubbed_from_result(self) -> None:
        registry = SecretRegistry({"API_KEY": "real-key-123"})
        tool = _dummy_tool(result="result contains real-key-123")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        assert "real-key-123" not in result
        assert "{{secret:API_KEY}}" in result

    @pytest.mark.asyncio
    async def test_no_secret_registry_no_substitution(self) -> None:
        captured_args: dict[str, Any] = {}

        async def _capture(args: dict[str, Any]) -> str:
            captured_args.update(args)
            return "ok"

        tool = Tool(
            name="test",
            description="t",
            parameters={"type": "object"},
            execute=_capture,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        await wrapped.execute({"url": "{{secret:API_KEY}}"})
        assert captured_args["url"] == "{{secret:API_KEY}}"


# ---------------------------------------------------------------------------
# TestTraceCallback
# ---------------------------------------------------------------------------


class TestTraceCallback:
    """Tests for trace callback invocation."""

    @pytest.mark.asyncio
    async def test_trace_callback_invoked(self) -> None:
        callback = AsyncMock()
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(result="traced"),
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=callback,
            session_id=_SESSION_ID,
        )
        await wrapped.execute({"key": "value"})
        callback.assert_awaited_once()
        call_args = callback.call_args[0]
        assert call_args[0] == "test_tool"  # tool name
        assert call_args[1] == {"key": "value"}  # original args
        assert call_args[2] == "traced"  # result
        assert isinstance(call_args[3], int)  # duration_ms
        assert call_args[3] >= 0

    @pytest.mark.asyncio
    async def test_no_trace_callback_no_error(self) -> None:
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(),
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        assert result == "ok"


# ---------------------------------------------------------------------------
# TestMutationTracking
# ---------------------------------------------------------------------------


class TestMutationTracking:
    """Tests for file mutation tracking."""

    @pytest.mark.asyncio
    async def test_file_mutation_tool_sets_flag(self) -> None:
        tracker: dict[str, bool] = {}
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(),
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            is_file_mutation=True,
            mutation_tracker=tracker,
        )
        await wrapped.execute({})
        assert tracker[_SESSION_ID] is True

    @pytest.mark.asyncio
    async def test_non_mutation_tool_does_not_set_flag(self) -> None:
        tracker: dict[str, bool] = {}
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(),
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            is_file_mutation=False,
            mutation_tracker=tracker,
        )
        await wrapped.execute({})
        assert _SESSION_ID not in tracker

    @pytest.mark.asyncio
    async def test_mutation_tracker_none_no_error(self) -> None:
        wrapped = wrap_tool_with_pipeline(
            tool=_dummy_tool(),
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            is_file_mutation=True,
            mutation_tracker=None,
        )
        result = await wrapped.execute({})
        assert result == "ok"


# ---------------------------------------------------------------------------
# TestWrapToolPreservation
# ---------------------------------------------------------------------------


class TestWrapToolPreservation:
    """Tests that wrapped tools preserve original metadata."""

    def test_wrapped_tool_preserves_name(self) -> None:
        original = _dummy_tool(name="my_tool")
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.name == "my_tool"

    def test_wrapped_tool_preserves_description(self) -> None:
        original = _dummy_tool()
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.description == "Test tool"

    def test_wrapped_tool_preserves_parameters(self) -> None:
        original = _dummy_tool()
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.parameters == {"type": "object"}


# ---------------------------------------------------------------------------
# TestWrapToolsForSession
# ---------------------------------------------------------------------------


class TestWrapToolsForSession:
    """Tests for wrap_tools_for_session."""

    def test_wraps_all_tools(self) -> None:
        tools = [_dummy_tool(name="a"), _dummy_tool(name="b"), _dummy_tool(name="c")]
        wrapped = wrap_tools_for_session(
            tools=tools,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert len(wrapped) == 3
        assert [t.name for t in wrapped] == ["a", "b", "c"]

    @pytest.mark.asyncio
    async def test_detects_start_task_by_name(self) -> None:
        tools = [_dummy_tool(name="start_task"), _dummy_tool(name="other")]
        wrapped = wrap_tools_for_session(
            tools=tools,
            scheduler_check=_failing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        # start_task is exempt from the active task check.
        result = await wrapped[0].execute({})
        assert result == "ok"
        # other is NOT exempt.
        with pytest.raises(ToolError, match="No active task for session"):
            await wrapped[1].execute({})

    @pytest.mark.asyncio
    async def test_detects_file_mutation_tools(self) -> None:
        tools = [
            _dummy_tool(name="write"),
            _dummy_tool(name="edit"),
            _dummy_tool(name="read"),
        ]
        tracker: dict[str, bool] = {}
        wrapped = wrap_tools_for_session(
            tools=tools,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            mutation_tracker=tracker,
        )
        # Call each tool and check mutation tracking.
        await wrapped[0].execute({})  # write
        assert _SESSION_ID in tracker

        tracker.clear()
        await wrapped[1].execute({})  # edit
        assert _SESSION_ID in tracker

        tracker.clear()
        await wrapped[2].execute({})  # read
        assert _SESSION_ID not in tracker


# ---------------------------------------------------------------------------
# TestFullPipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    """Integration test combining all pipeline features."""

    @pytest.mark.asyncio
    async def test_full_pipeline_integration(self) -> None:
        registry = SecretRegistry({"TOKEN": "secret-val-999"})
        callback = AsyncMock()
        tracker: dict[str, bool] = {}
        captured_args: dict[str, Any] = {}

        async def _execute(args: dict[str, Any]) -> str:
            captured_args.update(args)
            return "response includes secret-val-999"

        tool = Tool(
            name="write",
            description="Write tool",
            parameters={"type": "object"},
            execute=_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=callback,
            session_id=_SESSION_ID,
            is_file_mutation=True,
            mutation_tracker=tracker,
        )
        result = await wrapped.execute({"auth": "{{secret:TOKEN}}"})

        # Secret substituted in args.
        assert captured_args["auth"] == "secret-val-999"

        # Secret scrubbed from result.
        assert "secret-val-999" not in result
        assert "{{secret:TOKEN}}" in result

        # Mutation tracked.
        assert tracker[_SESSION_ID] is True

        # Trace callback invoked.
        callback.assert_awaited_once()
        call_args = callback.call_args[0]
        assert call_args[0] == "write"
        assert isinstance(call_args[3], int)
        assert call_args[3] >= 0
