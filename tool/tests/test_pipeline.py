"""Tests for the tool execution pipeline wrapper."""

from __future__ import annotations

import dataclasses
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
from orxtra.protocols import Tool, ToolError, ToolOutput
from orxtra.secrets import SecretRegistry
from orxtra.tool._pipeline import compose, wrap_tool_with_pipeline, wrap_tools_for_session
from orxtra.tool._scrub import scrub_data, scrub_text, scrub_tool_output

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "session-1"
_TASK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _dummy_tool(name: str = "test_tool", result: str = "ok") -> Tool:
    """Create a simple async tool for testing."""

    async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
        return ToolOutput(data=result, text=result)

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
    msg = "No active task for session"
    raise ToolError(msg)


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
        result = (await wrapped.execute({})).text
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
        result = (await wrapped.execute({})).text
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

        async def _capture(args: dict[str, Any]) -> ToolOutput[str]:
            captured_args.update(args)
            return ToolOutput(data="ok", text="ok")

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
        result = (await wrapped.execute({})).text
        assert "real-key-123" not in result
        assert "{{secret:API_KEY}}" in result

    @pytest.mark.asyncio
    async def test_no_secret_registry_no_substitution(self) -> None:
        captured_args: dict[str, Any] = {}

        async def _capture(args: dict[str, Any]) -> ToolOutput[str]:
            captured_args.update(args)
            return ToolOutput(data="ok", text="ok")

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
        result = (await wrapped.execute({})).text
        assert result == "ok"


# ---------------------------------------------------------------------------
# TestMutationTracking
# ---------------------------------------------------------------------------


class TestMutationTracking:
    """Tests for file mutation tracking."""

    @pytest.mark.asyncio
    async def test_file_mutation_tool_sets_flag(self) -> None:
        tracker: dict[str, set[str]] = {}
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
        assert tracker[_SESSION_ID] == {"__generic__"}

    @pytest.mark.asyncio
    async def test_non_mutation_tool_does_not_set_flag(self) -> None:
        tracker: dict[str, set[str]] = {}
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
        result = (await wrapped.execute({})).text
        assert result == "ok"


# ---------------------------------------------------------------------------
# TestTransientRetry
# ---------------------------------------------------------------------------


class TestTransientRetry:
    """Tests for transient OS error retry in the pipeline."""

    @pytest.mark.asyncio
    async def test_transient_error_retried_and_succeeds(self) -> None:
        """Tool that raises OSError(EIO) once then succeeds is retried."""
        import errno  # noqa: PLC0415

        call_count = 0

        async def _flaky_execute(args: dict[str, Any]) -> ToolOutput[str]:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise OSError(errno.EIO, "I/O error")
            return ToolOutput(data="success", text="success")

        tool = Tool(
            name="flaky",
            description="Flaky tool",
            parameters={"type": "object"},
            execute=_flaky_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = (await wrapped.execute({})).text
        assert result == "success"
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_non_transient_error_not_retried(self) -> None:
        """Non-transient OSError (e.g., ENOENT) propagates immediately."""
        import errno  # noqa: PLC0415

        call_count = 0

        async def _failing_execute(args: dict[str, Any]) -> ToolOutput[str]:
            nonlocal call_count
            call_count += 1
            raise OSError(errno.ENOENT, "No such file")

        tool = Tool(
            name="broken",
            description="Broken tool",
            parameters={"type": "object"},
            execute=_failing_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        with pytest.raises(OSError, match="No such file"):
            await wrapped.execute({})
        assert call_count == 1


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

    def test_wrapped_tool_preserves_suspending_true(self) -> None:
        """A tool with suspending=True retains the flag after pipeline wrapping."""

        async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="ok", text="ok")

        original = Tool(
            name="await_task",
            description="Suspending tool",
            parameters={"type": "object"},
            execute=_execute,
            suspending=True,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.suspending is True

    def test_wrapped_tool_preserves_suspending_false(self) -> None:
        """A tool with suspending=False (default) retains the flag after wrapping."""
        original = _dummy_tool()
        assert original.suspending is False
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.suspending is False


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
        result = (await wrapped[0].execute({})).text
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
        tracker: dict[str, set[str]] = {}
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
        tracker: dict[str, set[str]] = {}
        captured_args: dict[str, Any] = {}

        async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
            captured_args.update(args)
            return ToolOutput(data="response includes secret-val-999", text="response includes secret-val-999")

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
        result = (await wrapped.execute({"auth": "{{secret:TOKEN}}"})).text

        # Secret substituted in args.
        assert captured_args["auth"] == "secret-val-999"

        # Secret scrubbed from result.
        assert "secret-val-999" not in result
        assert "{{secret:TOKEN}}" in result

        # Mutation tracked.
        assert tracker[_SESSION_ID] == {"__generic__"}

        # Trace callback invoked.
        callback.assert_awaited_once()
        call_args = callback.call_args[0]
        assert call_args[0] == "write"
        assert isinstance(call_args[3], int)
        assert call_args[3] >= 0


# ---------------------------------------------------------------------------
# TestCompose
# ---------------------------------------------------------------------------


class TestCompose:
    """Tests for the compose function."""

    @pytest.mark.asyncio
    async def test_compose_on_wrapped_tool_calls_raw_execute(self) -> None:
        """compose bypasses the pipeline and calls the original execute."""
        captured: list[str] = []

        async def _raw_execute(args: dict[str, Any]) -> ToolOutput[str]:
            captured.append("raw")
            return ToolOutput(data="raw_result", text="raw_result")

        tool = Tool(
            name="test",
            description="t",
            parameters={"type": "object"},
            execute=_raw_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = (await compose(wrapped, {})).text
        assert result == "raw_result"
        assert captured == ["raw"]

    @pytest.mark.asyncio
    async def test_compose_on_unwrapped_tool_calls_execute_directly(self) -> None:
        """compose on an unwrapped tool just calls execute."""
        tool = _dummy_tool(result="direct")
        result = (await compose(tool, {})).text
        assert result == "direct"

    @pytest.mark.asyncio
    async def test_compose_on_double_wrapped_calls_innermost(self) -> None:
        """Double-wrapped tool's compose still calls the original raw execute."""
        call_log: list[str] = []

        async def _innermost(args: dict[str, Any]) -> ToolOutput[str]:
            call_log.append("innermost")
            return ToolOutput(data="inner_result", text="inner_result")

        tool = Tool(
            name="test",
            description="t",
            parameters={"type": "object"},
            execute=_innermost,
        )
        wrapped_once = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        wrapped_twice = wrap_tool_with_pipeline(
            tool=wrapped_once,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = (await compose(wrapped_twice, {})).text
        assert result == "inner_result"
        assert call_log == ["innermost"]

    @pytest.mark.asyncio
    async def test_compose_result_not_scrubbed(self) -> None:
        """compose bypasses secret scrubbing -- secrets pass through."""
        registry = SecretRegistry({"TOKEN": "secret-val-999"})
        tool = _dummy_tool(result="contains secret-val-999")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = (await compose(wrapped, {})).text
        # The raw execute returns the unscrubbed result.
        assert "secret-val-999" in result

    @pytest.mark.asyncio
    async def test_compose_doesnt_trigger_mutation_tracker(self) -> None:
        """compose bypasses the pipeline, so mutation tracking is skipped."""
        tracker: dict[str, set[str]] = {}
        tool = _dummy_tool(name="write", result="wrote")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            is_file_mutation=True,
            mutation_tracker=tracker,
        )
        await compose(wrapped, {})
        assert _SESSION_ID not in tracker


# ---------------------------------------------------------------------------
# TestNamespaceTagsPreservation
# ---------------------------------------------------------------------------


class TestNamespaceTagsPreservation:
    """Tests that namespace and tags are preserved through pipeline wrapping."""

    def test_namespace_preserved_through_wrapping(self) -> None:
        """A tool's namespace is preserved after pipeline wrapping."""

        async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="ok", text="ok")

        original = Tool(
            name="read",
            description="Read tool",
            parameters={"type": "object"},
            execute=_execute,
            namespace="fs.read",
            tags=frozenset({"readonly"}),
        )
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.namespace == "fs.read"

    def test_tags_preserved_through_wrapping(self) -> None:
        """A tool's tags are preserved after pipeline wrapping."""

        async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="ok", text="ok")

        original = Tool(
            name="git",
            description="Git tool",
            parameters={"type": "object"},
            execute=_execute,
            namespace="git",
            tags=frozenset({"readonly", "mutation"}),
        )
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.tags == frozenset({"readonly", "mutation"})

    def test_empty_namespace_preserved(self) -> None:
        """Default empty namespace is preserved after wrapping."""
        original = _dummy_tool()
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.namespace == ""

    def test_empty_tags_preserved(self) -> None:
        """Default empty tags are preserved after wrapping."""
        original = _dummy_tool()
        wrapped = wrap_tool_with_pipeline(
            tool=original,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped.tags == frozenset()

    def test_namespace_tags_preserved_through_wrap_tools_for_session(self) -> None:
        """wrap_tools_for_session preserves namespace and tags on all tools."""

        async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="ok", text="ok")

        tools = [
            Tool(
                name="read",
                description="Read",
                parameters={"type": "object"},
                execute=_execute,
                namespace="fs.read",
                tags=frozenset({"readonly"}),
            ),
            Tool(
                name="write",
                description="Write",
                parameters={"type": "object"},
                execute=_execute,
                namespace="fs.write",
                tags=frozenset({"mutation"}),
            ),
        ]
        wrapped = wrap_tools_for_session(
            tools=tools,
            scheduler_check=_passing_scheduler_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        assert wrapped[0].namespace == "fs.read"
        assert wrapped[0].tags == frozenset({"readonly"})
        assert wrapped[1].namespace == "fs.write"
        assert wrapped[1].tags == frozenset({"mutation"})


# ---------------------------------------------------------------------------
# TestStructuredDataScrubbing
# ---------------------------------------------------------------------------


@dataclasses.dataclass(frozen=True)
class _HttpResponse:
    """Simulated structured HTTP response data."""

    status: int
    headers: dict[str, str]
    body: str


class TestStructuredDataScrubbing:
    """Tests for scrubbing secrets from ToolOutput.data (structured data)."""

    @pytest.mark.asyncio
    async def test_secret_in_data_dict_scrubbed_by_pipeline(self) -> None:
        """A tool returning a dict with a secret value has it scrubbed."""
        registry = SecretRegistry({"API_KEY": "real-key-123"})

        async def _execute(args: dict[str, Any]) -> ToolOutput[dict[str, str]]:
            return ToolOutput(
                data={"Authorization": "Bearer real-key-123"},
                text="ok",
            )

        tool = Tool(
            name="http",
            description="HTTP tool",
            parameters={"type": "object"},
            execute=_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        # The secret must not appear in data.
        assert "real-key-123" not in str(result.data)
        assert "{{secret:API_KEY}}" in str(result.data)

    @pytest.mark.asyncio
    async def test_secret_in_dataclass_data_scrubbed_by_pipeline(self) -> None:
        """A tool returning a dataclass with a secret header has it scrubbed.

        After scrubbing, data is a dict (deserialized JSON) rather than the
        original dataclass -- acceptable per the spec.
        """
        registry = SecretRegistry({"TOKEN": "super-secret-42"})

        async def _execute(args: dict[str, Any]) -> ToolOutput[_HttpResponse]:
            return ToolOutput(
                data=_HttpResponse(
                    status=200,
                    headers={"X-Api-Key": "super-secret-42"},
                    body="response body with super-secret-42",
                ),
                text="HTTP 200 OK",
            )

        tool = Tool(
            name="http",
            description="HTTP tool",
            parameters={"type": "object"},
            execute=_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        # Secret must be scrubbed from both text and data.
        assert "super-secret-42" not in result.text
        assert "super-secret-42" not in str(result.data)
        # Placeholders should appear.
        assert "{{secret:TOKEN}}" in str(result.data)

    @pytest.mark.asyncio
    async def test_none_data_not_crashed(self) -> None:
        """A tool returning None data does not crash the scrub path."""
        registry = SecretRegistry({"KEY": "val-123"})

        async def _execute(args: dict[str, Any]) -> ToolOutput[None]:
            return ToolOutput(data=None, text="text with val-123")

        tool = Tool(
            name="test",
            description="t",
            parameters={"type": "object"},
            execute=_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        assert result.data is None
        assert "val-123" not in result.text
        assert "{{secret:KEY}}" in result.text

    @pytest.mark.asyncio
    async def test_string_data_scrubbed(self) -> None:
        """A tool returning a plain string as data has secrets scrubbed."""
        registry = SecretRegistry({"SECRET": "abc123"})

        async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="contains abc123", text="text with abc123")

        tool = Tool(
            name="test",
            description="t",
            parameters={"type": "object"},
            execute=_execute,
        )
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_scheduler_check,
            secret_registry=registry,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        assert "abc123" not in result.text
        assert "abc123" not in str(result.data)


# ---------------------------------------------------------------------------
# TestScrubModule
# ---------------------------------------------------------------------------


class TestScrubModule:
    """Unit tests for the shared _scrub module."""

    def test_scrub_text(self) -> None:
        registry = SecretRegistry({"KEY": "secret-value"})
        result = scrub_text(registry, "data: secret-value")
        assert "secret-value" not in result
        assert "{{secret:KEY}}" in result

    def test_scrub_data_dict(self) -> None:
        registry = SecretRegistry({"KEY": "secret-value"})
        result = scrub_data(registry, {"header": "Bearer secret-value"})
        assert isinstance(result, dict)
        assert "secret-value" not in str(result)
        assert "{{secret:KEY}}" in str(result)

    def test_scrub_data_none(self) -> None:
        registry = SecretRegistry({"KEY": "secret-value"})
        result = scrub_data(registry, None)
        assert result is None

    def test_scrub_data_dataclass(self) -> None:
        registry = SecretRegistry({"TOKEN": "tok-999"})
        data = _HttpResponse(
            status=200,
            headers={"Auth": "tok-999"},
            body="ok",
        )
        result = scrub_data(registry, data)
        assert isinstance(result, dict)
        assert "tok-999" not in str(result)
        assert "{{secret:TOKEN}}" in str(result)

    def test_scrub_data_list(self) -> None:
        registry = SecretRegistry({"KEY": "secret-value"})
        result = scrub_data(registry, ["secret-value", "safe"])
        assert isinstance(result, list)
        assert "secret-value" not in str(result)

    def test_scrub_tool_output(self) -> None:
        registry = SecretRegistry({"KEY": "secret-value"})
        output = ToolOutput(
            data={"x": "secret-value"},
            text="text secret-value",
        )
        result = scrub_tool_output(registry, output)
        assert "secret-value" not in result.text
        assert "secret-value" not in str(result.data)
        assert "{{secret:KEY}}" in result.text
        assert "{{secret:KEY}}" in str(result.data)
