from __future__ import annotations

from typing import Any

import httpx
import respx
from orxtra.protocols import Tool
from orxtra.protocols._results import ToolOutput
from orxtra.transport._events import (
    ContentBlock,
    Event,
    ToolExecuting,
    ToolUse,
    Usage,
)
from orxtra.transport._provider import RetryPolicy
from orxtra.transport._transport import Transport

from .test_transport import MockProvider


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
) -> Tool:
    if params is None:
        params = {
            "type": "object",
            "properties": {"x": {"type": "string"}},
            "required": ["x"],
        }

    async def execute(args: dict[str, Any]) -> ToolOutput[str]:
        return ToolOutput(data=f"result for {args}", text=f"result for {args}")

    return Tool(
        name=name,
        description=f"Tool {name}",
        parameters=params,
        execute=execute,
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


class TestToolExecutingEvent:
    @respx.mock
    async def test_emitted_before_tool_use(self) -> None:
        """ToolExecuting is emitted immediately before each tool execution."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        tool = _make_tool()
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
            transport, "Do it", **_default_send_kwargs(tools=[tool]),
        )

        executing_events = [e for e in events if isinstance(e, ToolExecuting)]
        assert len(executing_events) == 1
        assert executing_events[0].tool_name == "test_tool"
        assert executing_events[0].tool_input == {"x": "hello"}

        # ToolExecuting comes before ToolUse
        exec_idx = next(
            i for i, e in enumerate(events) if isinstance(e, ToolExecuting)
        )
        use_idx = next(
            i for i, e in enumerate(events) if isinstance(e, ToolUse)
        )
        assert exec_idx < use_idx

    @respx.mock
    async def test_emitted_for_each_tool(self) -> None:
        """Multiple tools each get a ToolExecuting event."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        async def exec_a(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="a", text="a")

        async def exec_b(args: dict[str, Any]) -> ToolOutput[str]:
            return ToolOutput(data="b", text="b")

        tool_a = _make_tool(name="tool_a")
        tool_a = Tool(
            name="tool_a",
            description="A",
            parameters=tool_a.parameters,
            execute=exec_a,
        )
        tool_b = Tool(
            name="tool_b",
            description="B",
            parameters=tool_a.parameters,
            execute=exec_b,
        )

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
            "Do both",
            **_default_send_kwargs(tools=[tool_a, tool_b]),
        )

        executing_events = [e for e in events if isinstance(e, ToolExecuting)]
        assert len(executing_events) == 2
        assert executing_events[0].tool_name == "tool_a"
        assert executing_events[1].tool_name == "tool_b"

    @respx.mock
    async def test_not_emitted_for_unknown_tool(self) -> None:
        """No ToolExecuting event for unrecognized tools (they error immediately)."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = MockProvider(
            responses=[
                (
                    [
                        ContentBlock(
                            type="tool_use",
                            tool_use_id="tu_1",
                            tool_name="nonexistent",
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

        executing_events = [e for e in events if isinstance(e, ToolExecuting)]
        assert len(executing_events) == 0

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].status == "error"

    @respx.mock
    async def test_not_emitted_for_validation_failure(self) -> None:
        """No ToolExecuting event when tool args fail validation."""
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
            transport, "Call it", **_default_send_kwargs(tools=[tool]),
        )

        executing_events = [e for e in events if isinstance(e, ToolExecuting)]
        assert len(executing_events) == 0

        tool_events = [e for e in events if isinstance(e, ToolUse)]
        assert len(tool_events) == 1
        assert tool_events[0].status == "error"
