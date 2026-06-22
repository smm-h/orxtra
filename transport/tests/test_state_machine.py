from __future__ import annotations

from typing import TYPE_CHECKING, Any

import httpx
import respx
from orxtra.protocols import Tool
from orxtra.protocols._results import Confirmation, ToolOutput
from orxtra.transport._events import (
    ContentBlock,
    Event,
    Result,
    SessionSuspended,
    StepStart,
    StreamDelta,
    StreamToolUse,
    StreamUsage,
    ToolUse,
    Usage,
)
from orxtra.transport._provider import RetryPolicy
from orxtra.transport._state_machine import Continuation
from orxtra.transport._transport import Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


# ---------------------------------------------------------------------------
# Mock provider (mirrors test_transport.py)
# ---------------------------------------------------------------------------


class MockProvider:
    """Provider that returns pre-configured responses in sequence."""

    def __init__(self, responses: list[tuple[list[ContentBlock], Usage]]) -> None:
        self._responses = list(responses)
        self._call_index = 0

    def build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        model: str,
    ) -> dict[str, Any]:
        return {
            "url": "https://mock.api/v1/messages",
            "headers": {"Authorization": "Bearer test"},
            "json_body": {"model": model, "messages": messages, "stream": False},
        }

    def parse_response(self, response: dict[str, Any]) -> list[ContentBlock]:
        blocks, _ = self._responses[self._call_index]
        return blocks

    async def parse_stream(  # type: ignore[override]
        self, byte_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[Event]:
        # Drain the byte stream (required by httpx)
        async for _ in byte_stream:
            pass
        # Yield streaming events from configured response blocks
        if self._call_index < len(self._responses):
            blocks, usage = self._responses[self._call_index]
            self._call_index += 1
            for block in blocks:
                if block.type == "text" and block.text is not None:
                    yield StreamDelta(text=block.text)
                elif block.type == "thinking" and block.text is not None:
                    from orxtra.transport._events import Thinking  # noqa: PLC0415
                    yield Thinking(text=block.text)
                elif block.type == "tool_use":
                    yield StreamToolUse(
                        tool_use_id=block.tool_use_id or "",
                        tool_name=block.tool_name or "",
                        tool_input=block.tool_input or {},
                    )
            yield StreamUsage(usage=usage)

    def extract_usage(self, response: dict[str, Any]) -> Usage:
        _, usage = self._responses[self._call_index]
        self._call_index += 1
        return usage

    def format_tool_result(
        self, tool_use_id: str, content: str, is_error: bool,
    ) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "tool_result",
            "tool_use_id": tool_use_id,
            "content": content,
        }
        if is_error:
            result["is_error"] = True
        return result

    def wrap_tool_results(
        self, results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [{"role": "user", "content": results}]

    def format_assistant_message(
        self, blocks: list[ContentBlock],
    ) -> dict[str, Any]:
        content: list[dict[str, Any]] = []
        for b in blocks:
            if b.type == "text":
                content.append({"type": "text", "text": b.text})
            elif b.type == "tool_use":
                content.append({
                    "type": "tool_use",
                    "id": b.tool_use_id,
                    "name": b.tool_name,
                    "input": b.tool_input,
                })
            elif b.type == "thinking":
                content.append({"type": "thinking", "thinking": b.text})
        return {"role": "assistant", "content": content}


class CapturingProvider(MockProvider):
    """MockProvider that records the messages it receives."""

    def __init__(self, responses: list[tuple[list[ContentBlock], Usage]]) -> None:
        super().__init__(responses)
        self.captured_messages: list[list[dict[str, Any]]] = []

    def build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        model: str,
    ) -> dict[str, Any]:
        self.captured_messages.append([dict(m) for m in messages])
        return super().build_request(messages, tools, system, model)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_URL = "https://mock.api/v1/messages"
_OK_RESPONSE = httpx.Response(200, json={"mock": True})


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_retries=3,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        jitter=False,
    )


def _make_tool(
    name: str = "test_tool",
    params: dict[str, Any] | None = None,
    execute_fn: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
    *,
    suspending: bool = False,
) -> Tool:
    if params is None:
        params = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }
    if execute_fn is None:

        async def execute_fn(args: dict[str, Any]) -> ToolOutput[str]:
            return f"result for {args}"

    return Tool(
        name=name,
        description=f"Tool {name}",
        parameters=params,
        execute=execute_fn,
        suspending=suspending,
    )


async def _collect(
    transport: Transport, message: str, **kwargs: Any,  # noqa: ANN401
) -> list[Event]:
    return [event async for event in transport.send(message, **kwargs)]


async def _collect_resume(
    transport: Transport,
    continuation: Continuation,
    await_result: str,
    **kwargs: Any,  # noqa: ANN401
) -> list[Event]:
    return [
        event
        async for event in transport.resume(continuation, await_result, **kwargs)
    ]


def _default_send_kwargs(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "model": "test-model",
        "system_prompt": "sys",
        "tools": [],
    }
    defaults.update(overrides)
    return defaults


def _default_resume_kwargs(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "model": "test-model",
        "system_prompt": "sys",
        "tools": [],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestNormalFlowUnchanged:
    """Verify that non-suspending tools follow the same state transitions."""

    @respx.mock
    async def test_calling_api_to_executing_tools_to_done(self) -> None:
        """Normal flow: CALLING_API -> EXECUTING_TOOLS -> CALLING_API -> DONE."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="tool output", text="tool output")

        tool = _make_tool(execute_fn=execute)
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="test_tool",
                            tool_input={"x": "hello"},
                        ),
                    ],
                    Usage(input_tokens=20, output_tokens=10),
                ),
                (
                    [ContentBlock(type="text", text="Done")],
                    Usage(input_tokens=30, output_tokens=15),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Do something", **_default_send_kwargs(tools=[tool]),
        )

        # Should have normal event sequence -- no SessionSuspended
        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 0

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Done"


class TestSuspendingToolTriggersSuspension:
    """A tool with suspending=True causes SUSPENDED state + SessionSuspended event."""

    @respx.mock
    async def test_suspending_tool_yields_session_suspended(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def await_execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="awaiting approval", text="awaiting approval")

        await_tool = _make_tool(
            name="await_tool", execute_fn=await_execute, suspending=True,
        )
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="await_tool",
                            tool_input={"x": "request"},
                        ),
                    ],
                    Usage(input_tokens=10, output_tokens=5),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Start task", **_default_send_kwargs(tools=[await_tool]),
        )

        # Should have ToolUse for the suspending tool
        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "await_tool"
        assert tool_events[0].status == "success"
        assert tool_events[0].output == "awaiting approval"

        # Should end with SessionSuspended
        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1
        assert suspended[0].session_id is not None
        assert isinstance(suspended[0].continuation, Continuation)

        # No Result event (session is suspended, not done)
        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 0


class TestResumeExecutesRemainingTools:
    """Resume executes remaining tool blocks from the batch."""

    @respx.mock
    async def test_resume_executes_remaining_and_continues(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        call_log: list[str] = []

        async def read_exec(args: dict[str, Any]) -> ToolOutput[str]:
            call_log.append("read")
            return ToolOutput(data="file content", text="file content")

        async def await_exec(args: dict[str, Any]) -> ToolOutput[str]:
            call_log.append("await")
            return ToolOutput(data="awaiting", text="awaiting")

        async def write_exec(args: dict[str, Any]) -> ToolOutput[str]:
            call_log.append("write")
            return ToolOutput(data="written", text="written")

        read_tool = _make_tool(name="read_tool", execute_fn=read_exec)
        await_tool = _make_tool(
            name="await_tool", execute_fn=await_exec, suspending=True,
        )
        write_tool = _make_tool(name="write_tool", execute_fn=write_exec)

        all_tools = [read_tool, await_tool, write_tool]

        # Provider: first response has 3 tool calls, second (after resume) is text
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="read_tool",
                            tool_input={"x": "file"},
                        ),
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_2",
                            tool_name="await_tool",
                            tool_input={"x": "approval"},
                        ),
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_3",
                            tool_name="write_tool",
                            tool_input={"x": "data"},
                        ),
                    ],
                    Usage(input_tokens=20, output_tokens=10),
                ),
                # After resume, all results sent, API returns text
                (
                    [ContentBlock(type="text", text="All done")],
                    Usage(input_tokens=40, output_tokens=20),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        # Phase 1: send() -- should suspend after read + await
        events = await _collect(
            transport,
            "Do all three",
            **_default_send_kwargs(tools=all_tools),
        )

        # read executed, await executed (suspending), write NOT yet
        assert call_log == ["read", "await"]

        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1
        continuation = suspended[0].continuation

        # Remaining should have the write_tool block
        assert len(continuation.remaining_blocks) == 1
        assert continuation.remaining_blocks[0].tool_name == "write_tool"

        # Phase 2: resume() -- executes remaining write tool, then API call
        resume_events = await _collect_resume(
            transport,
            continuation,
            "approved",
            **_default_resume_kwargs(tools=all_tools),
        )

        # write should now be executed
        assert call_log == ["read", "await", "write"]

        # Should have ToolUse for write and a Result
        tool_events = [e for e in resume_events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "write_tool"

        results = [e for e in resume_events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "All done"


class TestResumeReturnsToDone:
    """After resume, the state machine reaches DONE normally."""

    @respx.mock
    async def test_resume_continues_to_calling_api(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def await_exec(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="waiting", text="waiting")

        await_tool = _make_tool(
            name="await_tool", execute_fn=await_exec, suspending=True,
        )

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="await_tool",
                            tool_input={"x": "task"},
                        ),
                    ],
                    Usage(input_tokens=10, output_tokens=5),
                ),
                (
                    [ContentBlock(type="text", text="Completed")],
                    Usage(input_tokens=20, output_tokens=10),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        events = await _collect(
            transport, "Start", **_default_send_kwargs(tools=[await_tool]),
        )
        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1

        resume_events = await _collect_resume(
            transport,
            suspended[0].continuation,
            "done",
            **_default_resume_kwargs(tools=[await_tool]),
        )

        results = [e for e in resume_events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Completed"


class TestMultiToolBatchWithSuspend:
    """[read, await, write]: read executes, suspend, resume, write executes."""

    @respx.mock
    async def test_read_await_write_batch(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        executed: list[str] = []

        async def read_fn(args: dict[str, Any]) -> ToolOutput[str]:
            executed.append("read")
            return ToolOutput(data="read_result", text="read_result")

        async def await_fn(args: dict[str, Any]) -> ToolOutput[str]:
            executed.append("await")
            return ToolOutput(data="await_initiated", text="await_initiated")

        async def write_fn(args: dict[str, Any]) -> ToolOutput[str]:
            executed.append("write")
            return ToolOutput(data="write_result", text="write_result")

        read = _make_tool(name="read", execute_fn=read_fn)
        await_t = _make_tool(name="await_t", execute_fn=await_fn, suspending=True)
        write = _make_tool(name="write", execute_fn=write_fn)
        all_tools = [read, await_t, write]

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="read",
                            tool_input={"x": "1"},
                        ),
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_2",
                            tool_name="await_t",
                            tool_input={"x": "2"},
                        ),
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_3",
                            tool_name="write",
                            tool_input={"x": "3"},
                        ),
                    ],
                    Usage(input_tokens=30, output_tokens=15),
                ),
                (
                    [ContentBlock(type="text", text="Batch complete")],
                    Usage(input_tokens=50, output_tokens=25),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        # Send -- suspends after read + await
        send_events = await _collect(
            transport, "batch", **_default_send_kwargs(tools=all_tools),
        )
        assert executed == ["read", "await"]

        # Verify tool events before suspension
        tool_events = [e for e in send_events if isinstance(e, ToolUse)]
        assert len(tool_events) == 2
        assert tool_events[0].tool_name == "read"
        assert tool_events[1].tool_name == "await_t"

        suspended = [e for e in send_events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1

        # Resume -- executes write, then API returns text
        resume_events = await _collect_resume(
            transport,
            suspended[0].continuation,
            "external_result",
            **_default_resume_kwargs(tools=all_tools),
        )
        assert executed == ["read", "await", "write"]

        resume_tool_events = [e for e in resume_events if isinstance(e, ToolUse)]
        assert len(resume_tool_events) == 1
        assert resume_tool_events[0].tool_name == "write"

        results = [e for e in resume_events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Batch complete"


class TestHistoryAfterResume:
    """History after resume has correct message alternation."""

    @respx.mock
    async def test_history_alternation(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def await_exec(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="waiting", text="waiting")

        await_tool = _make_tool(
            name="await_tool", execute_fn=await_exec, suspending=True,
        )

        provider = CapturingProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="await_tool",
                            tool_input={"x": "1"},
                        ),
                    ],
                    Usage(),
                ),
                # Second API call after resume
                (
                    [ContentBlock(type="text", text="Final")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        events = await _collect(
            transport, "Go", **_default_send_kwargs(tools=[await_tool]),
        )
        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        continuation = suspended[0].continuation

        await _collect_resume(
            transport,
            continuation,
            "result",
            **_default_resume_kwargs(tools=[await_tool]),
        )

        # The second API call (after resume) should have proper message alternation
        # captured_messages[0] = first send's messages (user)
        # captured_messages[1] = resume's messages (user, assistant, user[tool_results])
        assert len(provider.captured_messages) == 2

        resume_msgs = provider.captured_messages[1]
        # Should be: user, assistant(tool_use), user(tool_results)
        assert resume_msgs[0]["role"] == "user"
        assert resume_msgs[1]["role"] == "assistant"
        assert resume_msgs[2]["role"] == "user"


class TestDoubleSuspension:
    """First await -> resume -> second await -> resume -> done."""

    @respx.mock
    async def test_double_suspend_resume(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        call_count = 0

        async def await_exec(args: dict[str, Any]) -> ToolOutput[str]:
            nonlocal call_count
            call_count += 1
            return ToolOutput(data=f"await_{call_count}", text=f"await_{call_count}")

        await_tool = _make_tool(
            name="await_tool", execute_fn=await_exec, suspending=True,
        )

        provider = MockProvider(
            responses=[
                # First API call: tool use
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="await_tool",
                            tool_input={"x": "first"},
                        ),
                    ],
                    Usage(input_tokens=10, output_tokens=5),
                ),
                # Second API call (after first resume): another tool use
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_2",
                            tool_name="await_tool",
                            tool_input={"x": "second"},
                        ),
                    ],
                    Usage(input_tokens=20, output_tokens=10),
                ),
                # Third API call (after second resume): text
                (
                    [ContentBlock(type="text", text="All done")],
                    Usage(input_tokens=30, output_tokens=15),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        # First send -> first suspension
        events1 = await _collect(
            transport, "Start", **_default_send_kwargs(tools=[await_tool]),
        )
        suspended1 = [e for e in events1 if isinstance(e, SessionSuspended)]
        assert len(suspended1) == 1
        assert call_count == 1

        # First resume -> second suspension
        events2 = await _collect_resume(
            transport,
            suspended1[0].continuation,
            "first_result",
            **_default_resume_kwargs(tools=[await_tool]),
        )
        suspended2 = [e for e in events2 if isinstance(e, SessionSuspended)]
        assert len(suspended2) == 1
        assert call_count == 2

        # Second resume -> done
        events3 = await _collect_resume(
            transport,
            suspended2[0].continuation,
            "second_result",
            **_default_resume_kwargs(tools=[await_tool]),
        )
        results = [e for e in events3 if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "All done"
        assert call_count == 2  # Only called during first two rounds


class TestSuspendIsLastToolInBatch:
    """Suspend is the last (or only) tool in the batch -- no remaining tools."""

    @respx.mock
    async def test_no_remaining_tools_after_suspension(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def await_exec(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="waiting", text="waiting")

        await_tool = _make_tool(
            name="await_tool", execute_fn=await_exec, suspending=True,
        )

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="await_tool",
                            tool_input={"x": "only"},
                        ),
                    ],
                    Usage(input_tokens=10, output_tokens=5),
                ),
                (
                    [ContentBlock(type="text", text="Done")],
                    Usage(input_tokens=20, output_tokens=10),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        events = await _collect(
            transport, "Do", **_default_send_kwargs(tools=[await_tool]),
        )

        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1
        assert len(suspended[0].continuation.remaining_blocks) == 0
        assert len(suspended[0].continuation.executed_results) == 1

        # Resume with no remaining tools
        resume_events = await _collect_resume(
            transport,
            suspended[0].continuation,
            "result",
            **_default_resume_kwargs(tools=[await_tool]),
        )

        results = [e for e in resume_events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Done"


class TestContinuationPreservesSessionId:
    """Continuation.session_id matches the original session."""

    @respx.mock
    async def test_session_id_preserved(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def await_exec(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="waiting", text="waiting")

        await_tool = _make_tool(
            name="await_tool", execute_fn=await_exec, suspending=True,
        )

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="await_tool",
                            tool_input={"x": "1"},
                        ),
                    ],
                    Usage(),
                ),
                (
                    [ContentBlock(type="text", text="Done")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())

        events = await _collect(
            transport,
            "Go",
            **_default_send_kwargs(tools=[await_tool], session_id="my-session"),
        )

        step_start = next(e for e in events if isinstance(e, StepStart))
        assert step_start.session_id == "my-session"

        suspended = [e for e in events if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1
        assert suspended[0].session_id == "my-session"
        assert suspended[0].continuation.session_id == "my-session"
