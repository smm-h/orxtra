from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import httpx
import respx
from orxtra.protocols import Confirmation, Tool, ToolError, ToolOutput
from orxtra.transport._events import (
    ApiRetry,
    ContentBlock,
    Error,
    Event,
    Result,
    StepFinish,
    StepStart,
    StreamDelta,
    StreamToolUse,
    StreamUsage,
    Text,
    Thinking,
    ToolUse,
    Usage,
)
from orxtra.transport._provider import RetryPolicy
from orxtra.transport._transport import Transport

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable


# ---------------------------------------------------------------------------
# Mock provider
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
                content.append(
                    {
                        "type": "tool_use",
                        "id": b.tool_use_id,
                        "name": b.tool_name,
                        "input": b.tool_input,
                    }
                )
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


def _retry_policy(
    max_retries: int = 3,
    base: float = 0.001,
    max_backoff: float = 0.01,
    *,
    jitter: bool = False,
) -> RetryPolicy:
    return RetryPolicy(
        max_retries=max_retries,
        backoff_base_seconds=base,
        backoff_max_seconds=max_backoff,
        jitter=jitter,
    )


def _make_tool(
    name: str = "test_tool",
    params: dict[str, Any] | None = None,
    execute_fn: Callable[[dict[str, Any]], Awaitable[str]] | None = None,
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
    )


async def _collect(
    transport: Transport, message: str, **kwargs: Any,  # noqa: ANN401
) -> list[Event]:
    return [event async for event in transport.send(message, **kwargs)]


def _default_send_kwargs(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
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


class TestSimpleTextResponse:
    @respx.mock
    async def test_event_sequence(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="Hello world")],
                    Usage(input_tokens=10, output_tokens=5),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        assert isinstance(events[0], StepStart)
        # Streaming always on: StreamDelta comes first, then StreamUsage,
        # then reconstructed Text
        assert isinstance(events[1], StreamDelta)
        assert events[1].text == "Hello world"
        assert isinstance(events[2], StreamUsage)

        text_events = [e for e in events if isinstance(e, Text)]
        assert len(text_events) == 1
        assert text_events[0].text == "Hello world"

        finish_events = [e for e in events if isinstance(e, StepFinish)]
        assert len(finish_events) == 1
        assert finish_events[0].input_tokens == 10
        assert finish_events[0].output_tokens == 5

        result_events = [e for e in events if isinstance(e, Result)]
        assert len(result_events) == 1
        assert result_events[0].text == "Hello world"
        assert result_events[0].total_input_tokens == 10
        assert result_events[0].total_output_tokens == 5


class TestToolCallLoop:
    @respx.mock
    async def test_single_tool_call_then_text(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="tool output", text="tool output")

        tool = _make_tool(execute_fn=execute)
        provider = MockProvider(
            responses=[
                # First response: tool use
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
                # Second response: text
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

        assert isinstance(events[0], StepStart)

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].tool_name == "test_tool"
        assert tool_events[0].status == "success"
        assert tool_events[0].output == "tool output"

        text_events = [e for e in events if isinstance(e, Text)]
        assert len(text_events) == 1
        assert text_events[0].text == "Done"

        result = next(e for e in events if isinstance(e, Result))
        assert result.tool_calls == 1


class TestMultiToolResponse:
    @respx.mock
    async def test_two_tools_in_one_response(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        call_log: list[str] = []

        async def exec_a(args: dict[str, Any]) -> ToolOutput[str]:
            call_log.append("a")
            return ToolOutput(data="result_a", text="result_a")

        async def exec_b(args: dict[str, Any]) -> ToolOutput[str]:
            call_log.append("b")
            return ToolOutput(data="result_b", text="result_b")

        tool_a = _make_tool(name="tool_a", execute_fn=exec_a)
        tool_b = _make_tool(name="tool_b", execute_fn=exec_b)

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="tool_a",
                            tool_input={"x": "1"},
                        ),
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_2",
                            tool_name="tool_b",
                            tool_input={"x": "2"},
                        ),
                    ],
                    Usage(input_tokens=10, output_tokens=5),
                ),
                (
                    [ContentBlock(type="text", text="All done")],
                    Usage(input_tokens=15, output_tokens=8),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Do two things", **_default_send_kwargs(tools=[tool_a, tool_b]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 2
        assert tool_events[0].tool_name == "tool_a"
        assert tool_events[0].output == "result_a"
        assert tool_events[1].tool_name == "tool_b"
        assert tool_events[1].output == "result_b"
        assert call_log == ["a", "b"]

        result = next(e for e in events if isinstance(e, Result))
        assert result.tool_calls == 2


class TestToolExecutionError:
    @respx.mock
    async def test_tool_error_propagated(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def failing_execute(args: dict[str, Any]) -> ToolOutput[str]:
            msg = "Permission denied"
            raise ToolError(msg)

        tool = _make_tool(execute_fn=failing_execute)
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="test_tool",
                            tool_input={"x": "val"},
                        ),
                    ],
                    Usage(),
                ),
                (
                    [ContentBlock(type="text", text="Handled error")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Try it", **_default_send_kwargs(tools=[tool]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].status == "error"
        assert tool_events[0].error == "Permission denied"
        assert tool_events[0].output == ""


class TestToolArgumentValidationFailure:
    @respx.mock
    async def test_missing_required_field(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        tool = _make_tool()  # requires field "x"
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="test_tool",
                            tool_input={},  # missing "x"
                        ),
                    ],
                    Usage(),
                ),
                (
                    [ContentBlock(type="text", text="Recovered")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Call tool", **_default_send_kwargs(tools=[tool]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].status == "error"
        assert "Missing required field" in (tool_events[0].error or "")
        assert "x" in (tool_events[0].error or "")


class TestSessionManagement:
    @respx.mock
    async def test_new_id_generated_when_none(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="Hi")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Hello", **_default_send_kwargs(session_id=None),
        )

        step_start = next(e for e in events if isinstance(e, StepStart))
        result = next(e for e in events if isinstance(e, Result))
        # Both share the same auto-generated session_id
        assert step_start.session_id == result.session_id
        # It should be a non-empty string (UUIDv7)
        assert len(step_start.session_id) > 0

    @respx.mock
    async def test_reuse_provided_id(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="Hi")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport,
            "Hello",
            **_default_send_kwargs(session_id="existing-id"),
        )

        step_start = next(e for e in events if isinstance(e, StepStart))
        result = next(e for e in events if isinstance(e, Result))
        assert step_start.session_id == "existing-id"
        assert result.session_id == "existing-id"


class TestConversationHistory:
    @respx.mock
    async def test_history_accumulates_across_sends(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = CapturingProvider(
            responses=[
                # First send
                (
                    [ContentBlock(type="text", text="First reply")],
                    Usage(),
                ),
                # Second send
                (
                    [ContentBlock(type="text", text="Second reply")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        sid = "conv-session"

        await _collect(
            transport, "Message 1", **_default_send_kwargs(session_id=sid),
        )
        await _collect(
            transport, "Message 2", **_default_send_kwargs(session_id=sid),
        )

        # First call should have 1 message (user)
        assert len(provider.captured_messages[0]) == 1
        assert provider.captured_messages[0][0]["role"] == "user"
        assert provider.captured_messages[0][0]["content"] == "Message 1"

        # Second call should have 3 messages (user, assistant, user)
        assert len(provider.captured_messages[1]) == 3
        assert provider.captured_messages[1][0]["role"] == "user"
        assert provider.captured_messages[1][1]["role"] == "assistant"
        assert provider.captured_messages[1][2]["role"] == "user"
        assert provider.captured_messages[1][2]["content"] == "Message 2"


class TestRetryOn429:
    @respx.mock
    async def test_retries_then_succeeds(self) -> None:
        response_iter = iter([
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="Hello")],
                    Usage(input_tokens=5, output_tokens=3),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 1
        assert retry_events[0].status_code == 429

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Hello"


class TestRetryOn500:
    @respx.mock
    async def test_retries_then_succeeds(self) -> None:
        response_iter = iter([
            httpx.Response(500, text="internal error"),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 1
        assert retry_events[0].status_code == 500

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1


class TestRetryExhausted:
    @respx.mock
    async def test_all_retries_fail(self) -> None:
        respx.post(_MOCK_URL).mock(
            return_value=httpx.Response(500, text="always failing"),
        )

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="never reached")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(
            provider=provider, retry_policy=_retry_policy(max_retries=2),
        )
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        # 2 retries (attempts 1 and 2 out of 3 total attempts)
        assert len(retry_events) == 2

        error_events = [e for e in events if isinstance(e, Error)]
        assert len(error_events) == 1
        assert error_events[0].name == "max_retries_exceeded"
        assert "3 attempts" in error_events[0].message

        # No Result event since all attempts failed
        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 0


class TestNonTransientError401:
    @respx.mock
    async def test_no_retry_on_401(self) -> None:
        respx.post(_MOCK_URL).mock(
            return_value=httpx.Response(401, text="unauthorized"),
        )

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="never reached")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 0

        error_events = [e for e in events if isinstance(e, Error)]
        assert len(error_events) == 1
        assert error_events[0].name == "api_error"
        assert "401" in error_events[0].message


class TestNonTransientError400:
    @respx.mock
    async def test_no_retry_on_400(self) -> None:
        respx.post(_MOCK_URL).mock(
            return_value=httpx.Response(400, text="bad request"),
        )

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="never reached")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 0

        error_events = [e for e in events if isinstance(e, Error)]
        assert len(error_events) == 1
        assert error_events[0].name == "api_error"
        assert "400" in error_events[0].message


class TestStreamingAlwaysOn:
    @respx.mock
    async def test_stream_deltas_always_emitted(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="chunk one")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport,
            "Hi",
            **_default_send_kwargs(),
        )

        stream_deltas = [e for e in events if isinstance(e, StreamDelta)]
        text_events = [e for e in events if isinstance(e, Text)]
        assert len(stream_deltas) == 1
        assert stream_deltas[0].text == "chunk one"
        assert len(text_events) == 1
        assert text_events[0].text == "chunk one"


class TestToolUseDurationMs:
    @respx.mock
    async def test_duration_is_recorded(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def slow_execute(args: dict[str, Any]) -> ToolOutput[str]:
            await asyncio.sleep(0.01)
            return ToolOutput(data="done", text="done")

        tool = _make_tool(execute_fn=slow_execute)
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="test_tool",
                            tool_input={"x": "val"},
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
            transport, "Go", **_default_send_kwargs(tools=[tool]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].duration_ms >= 0

    @respx.mock
    async def test_duration_on_error(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def error_execute(args: dict[str, Any]) -> ToolOutput[str]:
            await asyncio.sleep(0.01)
            msg = "fail"
            raise ToolError(msg)

        tool = _make_tool(execute_fn=error_execute)
        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="test_tool",
                            tool_input={"x": "val"},
                        ),
                    ],
                    Usage(),
                ),
                (
                    [ContentBlock(type="text", text="Handled")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Go", **_default_send_kwargs(tools=[tool]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].status == "error"
        assert tool_events[0].duration_ms >= 0


class TestResultAggregatesTokens:
    @respx.mock
    async def test_tokens_summed_across_api_calls(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def execute(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="ok", text="ok")

        tool = _make_tool(execute_fn=execute)
        provider = MockProvider(
            responses=[
                # First API call (tool use)
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="test_tool",
                            tool_input={"x": "1"},
                        ),
                    ],
                    Usage(
                        input_tokens=100,
                        output_tokens=50,
                        reasoning_tokens=10,
                        cache_read_tokens=20,
                        cache_write_tokens=5,
                    ),
                ),
                # Second API call (text response)
                (
                    [ContentBlock(type="text", text="Final")],
                    Usage(
                        input_tokens=200,
                        output_tokens=80,
                        reasoning_tokens=15,
                        cache_read_tokens=30,
                        cache_write_tokens=10,
                    ),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Sum it", **_default_send_kwargs(tools=[tool]),
        )

        result = next(e for e in events if isinstance(e, Result))
        assert result.total_input_tokens == 300
        assert result.total_output_tokens == 130
        assert result.total_reasoning_tokens == 25
        assert result.total_cache_read_tokens == 50
        assert result.total_cache_write_tokens == 15
        assert result.tool_calls == 1

        # StepFinish should reflect only the last API call's tokens
        step_finish = next(e for e in events if isinstance(e, StepFinish))
        assert step_finish.input_tokens == 200
        assert step_finish.output_tokens == 80


class TestEmptyToolsList:
    @respx.mock
    async def test_no_tools_text_response(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="Just text")],
                    Usage(input_tokens=5, output_tokens=3),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs(tools=[]))

        assert isinstance(events[0], StepStart)
        # Streaming always on: StreamDelta before Text
        assert isinstance(events[1], StreamDelta)
        assert events[1].text == "Just text"

        text_events = [e for e in events if isinstance(e, Text)]
        assert len(text_events) == 1
        assert text_events[0].text == "Just text"

        result = next(e for e in events if isinstance(e, Result))
        assert result.text == "Just text"
        assert result.tool_calls == 0


class TestUnknownToolName:
    @respx.mock
    async def test_unknown_tool_returns_error(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="nonexistent_tool",
                            tool_input={"x": "val"},
                        ),
                    ],
                    Usage(),
                ),
                (
                    [ContentBlock(type="text", text="Recovered")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Call it", **_default_send_kwargs(tools=[]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].status == "error"
        assert "Unknown tool" in (tool_events[0].error or "")
        assert tool_events[0].tool_name == "nonexistent_tool"


class TestThinkingBlocks:
    @respx.mock
    async def test_thinking_emitted_before_text(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(type="thinking", text="Let me think..."),
                        ContentBlock(type="text", text="Answer"),
                    ],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Think", **_default_send_kwargs())

        # Thinking comes before Text in event order
        thinking_idx = next(
            i for i, e in enumerate(events) if isinstance(e, Thinking)
        )
        text_idx = next(i for i, e in enumerate(events) if isinstance(e, Text))
        assert thinking_idx < text_idx

        thinking_events = [e for e in events if isinstance(e, Thinking)]
        assert len(thinking_events) == 1
        assert thinking_events[0].text == "Let me think..."


class TestRetryOn502:
    @respx.mock
    async def test_retries_then_succeeds(self) -> None:
        response_iter = iter([
            httpx.Response(502, text="bad gateway"),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 1
        assert retry_events[0].status_code == 502

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1


class TestRetryOn503:
    @respx.mock
    async def test_retries_then_succeeds(self) -> None:
        response_iter = iter([
            httpx.Response(503, text="service unavailable"),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 1
        assert retry_events[0].status_code == 503

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1


class TestMultiRoundToolCalls:
    @respx.mock
    async def test_two_rounds_then_text(self) -> None:
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def exec_a(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="result_a", text="result_a")

        async def exec_b(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="result_b", text="result_b")

        tool_a = _make_tool(name="tool_a", execute_fn=exec_a)
        tool_b = _make_tool(name="tool_b", execute_fn=exec_b)

        provider = MockProvider(
            responses=[
                # Round 1: tool_a
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="tool_a",
                            tool_input={"x": "1"},
                        ),
                    ],
                    Usage(input_tokens=10, output_tokens=5),
                ),
                # Round 2: tool_b
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_2",
                            tool_name="tool_b",
                            tool_input={"x": "2"},
                        ),
                    ],
                    Usage(input_tokens=15, output_tokens=8),
                ),
                # Round 3: text
                (
                    [ContentBlock(type="text", text="All rounds done")],
                    Usage(input_tokens=20, output_tokens=10),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(
            transport, "Do it", **_default_send_kwargs(tools=[tool_a, tool_b]),
        )

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 2
        assert tool_events[0].tool_name == "tool_a"
        assert tool_events[1].tool_name == "tool_b"

        text_events = [e for e in events if isinstance(e, Text)]
        assert len(text_events) == 1
        assert text_events[0].text == "All rounds done"

        result = next(e for e in events if isinstance(e, Result))
        assert result.tool_calls == 2


class TestMaxRetriesZero:
    @respx.mock
    async def test_no_retries_on_failure(self) -> None:
        respx.post(_MOCK_URL).mock(
            return_value=httpx.Response(500, text="server error"),
        )

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="never reached")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(
            provider=provider, retry_policy=_retry_policy(max_retries=0),
        )
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        retry_events = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retry_events) == 0

        error_events = [e for e in events if isinstance(e, Error)]
        assert len(error_events) == 1
        assert error_events[0].name == "max_retries_exceeded"
        assert "1 attempts" in error_events[0].message

        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 0
