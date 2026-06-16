from __future__ import annotations

import asyncio
import random
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

import httpx
import uuid6

from ._events import (
    ApiRetry,
    ContentBlock,
    Error,
    Event,
    Result,
    SessionSuspended,
    StepFinish,
    StepStart,
    StreamDelta,
    Text,
    Thinking,
    ToolUse,
    Usage,
)
from ._state_machine import Continuation, TransportState

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ._provider import Provider, RetryPolicy

from orxt.protocols import Tool, ToolError

_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503})


def _validate_tool_args(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
    required = schema.get("required", [])
    for f in required:
        if f not in args:
            return f"Missing required field: {f}"
    properties = schema.get("properties", {})
    if not schema.get("additionalProperties", True):
        for key in args:
            if key not in properties:
                return f"Unknown field: {key}"
    return None


@dataclass
class _StepContext:
    """Mutable state carried across step() calls within a single send()."""

    history: list[dict[str, Any]]
    tool_specs: list[dict[str, Any]]
    tool_map: dict[str, Tool]
    model: str
    system_prompt: str
    session_id: str
    stream_deltas: bool
    total_input: int = 0
    total_output: int = 0
    total_reasoning: int = 0
    total_cache_read: int = 0
    total_cache_write: int = 0
    total_tool_calls: int = 0
    # Set during CALLING_API, consumed during EXECUTING_TOOLS
    pending_tool_blocks: list[ContentBlock] = field(default_factory=list)
    # Last API call's usage, needed for StepFinish
    last_usage: Usage | None = None
    # Tool results collected before suspension (for resume to combine)
    suspended_tool_results: list[dict[str, Any]] = field(default_factory=list)


class Transport:
    def __init__(self, provider: Provider, retry_policy: RetryPolicy) -> None:
        self._provider = provider
        self._retry = retry_policy
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    async def send(  # noqa: PLR0913
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        if session_id is None:
            session_id = str(uuid6.uuid7())

        history = self._sessions.setdefault(session_id, [])
        history.append({"role": "user", "content": message})

        tool_specs = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ]
        tool_map = {t.name: t for t in tools}

        yield StepStart(session_id=session_id)

        ctx = _StepContext(
            history=history,
            tool_specs=tool_specs,
            tool_map=tool_map,
            model=model,
            system_prompt=system_prompt,
            session_id=session_id,
            stream_deltas=stream_deltas,
        )

        state = TransportState.CALLING_API
        while state not in (TransportState.DONE, TransportState.SUSPENDED):
            state, events = await self.step(state, ctx)
            for event in events:
                yield event

        if state == TransportState.SUSPENDED:
            yield SessionSuspended(
                continuation=Continuation(
                    executed_results=ctx.suspended_tool_results,
                    remaining_blocks=ctx.pending_tool_blocks,
                    session_id=ctx.session_id,
                    messages=list(ctx.history),
                ),
                session_id=ctx.session_id,
            )

    async def resume(  # noqa: PLR0913
        self,
        continuation: Continuation,
        await_result: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        """Resume from suspension. Executes remaining tools, then continues the API loop."""
        session_id = continuation.session_id
        if session_id is None:
            msg = "Continuation has no session_id"
            raise ValueError(msg)

        # Restore history from continuation
        history = continuation.messages
        self._sessions[session_id] = history

        tool_specs = [
            {"name": t.name, "description": t.description, "parameters": t.parameters}
            for t in tools
        ]
        tool_map = {t.name: t for t in tools}

        ctx = _StepContext(
            history=history,
            tool_specs=tool_specs,
            tool_map=tool_map,
            model=model,
            system_prompt=system_prompt,
            session_id=session_id,
            stream_deltas=stream_deltas,
        )

        # Build combined tool results: pre-suspend results + remaining tools' results
        all_tool_results = list(continuation.executed_results)

        # Execute remaining tool blocks
        remaining_results: list[dict[str, Any]] = []

        for block in continuation.remaining_blocks:
            ctx.total_tool_calls += 1
            tool_name = block.tool_name or ""
            tool_input = block.tool_input or {}
            tool_use_id = block.tool_use_id or ""

            tool = ctx.tool_map.get(tool_name)
            if tool is None:
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_input,
                    output="",
                    status="error",
                    error=f"Unknown tool: {tool_name}",
                )
                remaining_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=f"Error: Unknown tool: {tool_name}",
                        is_error=True,
                    )
                )
                continue

            validation_error = _validate_tool_args(tool_input, tool.parameters)
            if validation_error is not None:
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_input,
                    output="",
                    status="error",
                    error=validation_error,
                )
                remaining_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=f"Error: {validation_error}",
                        is_error=True,
                    )
                )
                continue

            start = time.monotonic_ns()
            try:
                result_text = await tool.execute(tool_input)
                duration_ms = (time.monotonic_ns() - start) // 1_000_000
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_input,
                    output=result_text,
                    status="success",
                    duration_ms=duration_ms,
                )
                remaining_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=result_text,
                        is_error=False,
                    )
                )
            except ToolError as e:
                duration_ms = (time.monotonic_ns() - start) // 1_000_000
                error_msg = str(e)
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_input,
                    output="",
                    status="error",
                    error=error_msg,
                    duration_ms=duration_ms,
                )
                remaining_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=f"Error: {error_msg}",
                        is_error=True,
                    )
                )

        # Combine all results into one user message
        combined_results = all_tool_results + remaining_results
        ctx.history.append({"role": "user", "content": combined_results})

        # Continue the state machine from CALLING_API
        state = TransportState.CALLING_API
        while state not in (TransportState.DONE, TransportState.SUSPENDED):
            state, events = await self.step(state, ctx)
            for event in events:
                yield event

        if state == TransportState.SUSPENDED:
            yield SessionSuspended(
                continuation=Continuation(
                    executed_results=ctx.suspended_tool_results,
                    remaining_blocks=ctx.pending_tool_blocks,
                    session_id=ctx.session_id,
                    messages=list(ctx.history),
                ),
                session_id=ctx.session_id,
            )

    async def step(
        self,
        state: TransportState,
        ctx: _StepContext,
    ) -> tuple[TransportState, list[Event]]:
        """Execute one state transition. Returns (next_state, events)."""
        if state == TransportState.CALLING_API:
            return await self._step_calling_api(ctx)
        if state == TransportState.EXECUTING_TOOLS:
            return await self._step_executing_tools(ctx)
        # DONE / SUSPENDED: no-op (should not be called)
        return (state, [])

    async def _step_calling_api(
        self,
        ctx: _StepContext,
    ) -> tuple[TransportState, list[Event]]:
        events: list[Event] = []

        request = self._provider.build_request(
            messages=ctx.history,
            tools=ctx.tool_specs,
            system=ctx.system_prompt,
            model=ctx.model,
        )

        json_body = request["json_body"]
        json_body["stream"] = False

        response, retry_events = await self._send_with_retry(
            url=request["url"],
            headers=request["headers"],
            json_body=json_body,
        )
        events.extend(retry_events)
        if response is None:
            return (TransportState.DONE, events)

        response_data = response.json()
        blocks = self._provider.parse_response(response_data)
        usage = self._provider.extract_usage(response_data)

        ctx.total_input += usage.input_tokens
        ctx.total_output += usage.output_tokens
        ctx.total_reasoning += usage.reasoning_tokens
        ctx.total_cache_read += usage.cache_read_tokens
        ctx.total_cache_write += usage.cache_write_tokens
        ctx.last_usage = usage

        text_blocks: list[ContentBlock] = []
        thinking_blocks: list[ContentBlock] = []
        tool_use_blocks: list[ContentBlock] = []

        for block in blocks:
            if block.type == "text":
                text_blocks.append(block)
            elif block.type == "thinking":
                thinking_blocks.append(block)
            elif block.type == "tool_use":
                tool_use_blocks.append(block)

        for block in thinking_blocks:
            if block.text is not None:
                events.append(Thinking(text=block.text))

        for block in text_blocks:
            if block.text is not None:
                if ctx.stream_deltas:
                    events.append(StreamDelta(text=block.text))
                events.append(Text(text=block.text))

        if tool_use_blocks:
            ctx.history.append(self._provider.format_assistant_message(blocks))
            ctx.pending_tool_blocks = tool_use_blocks
            return (TransportState.EXECUTING_TOOLS, events)

        # Text-only response: finish
        full_text = " ".join(
            block.text for block in text_blocks if block.text is not None
        )
        ctx.history.append(self._provider.format_assistant_message(blocks))

        events.append(
            StepFinish(
                reason="end_turn",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
        )
        events.append(
            Result(
                text=full_text,
                session_id=ctx.session_id,
                total_input_tokens=ctx.total_input,
                total_output_tokens=ctx.total_output,
                total_reasoning_tokens=ctx.total_reasoning,
                total_cache_read_tokens=ctx.total_cache_read,
                total_cache_write_tokens=ctx.total_cache_write,
                tool_calls=ctx.total_tool_calls,
            )
        )
        return (TransportState.DONE, events)

    async def _step_executing_tools(
        self,
        ctx: _StepContext,
    ) -> tuple[TransportState, list[Event]]:
        events: list[Event] = []
        tool_results: list[dict[str, Any]] = []

        for i, block in enumerate(ctx.pending_tool_blocks):
            ctx.total_tool_calls += 1
            tool_name = block.tool_name or ""
            tool_input = block.tool_input or {}
            tool_use_id = block.tool_use_id or ""

            tool = ctx.tool_map.get(tool_name)
            if tool is None:
                events.append(
                    ToolUse(
                        tool_name=tool_name,
                        input=tool_input,
                        output="",
                        status="error",
                        error=f"Unknown tool: {tool_name}",
                    )
                )
                tool_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=f"Error: Unknown tool: {tool_name}",
                        is_error=True,
                    )
                )
                continue

            validation_error = _validate_tool_args(tool_input, tool.parameters)
            if validation_error is not None:
                events.append(
                    ToolUse(
                        tool_name=tool_name,
                        input=tool_input,
                        output="",
                        status="error",
                        error=validation_error,
                    )
                )
                tool_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=f"Error: {validation_error}",
                        is_error=True,
                    )
                )
                continue

            # Check for suspension BEFORE execution
            if tool.suspending:
                start = time.monotonic_ns()
                try:
                    result_text = await tool.execute(tool_input)
                    duration_ms = (time.monotonic_ns() - start) // 1_000_000
                    events.append(
                        ToolUse(
                            tool_name=tool_name,
                            input=tool_input,
                            output=result_text,
                            status="success",
                            duration_ms=duration_ms,
                        )
                    )
                    tool_results.append(
                        self._provider.format_tool_result(
                            tool_use_id=tool_use_id,
                            content=result_text,
                            is_error=False,
                        )
                    )
                except ToolError as e:
                    duration_ms = (time.monotonic_ns() - start) // 1_000_000
                    error_msg = str(e)
                    events.append(
                        ToolUse(
                            tool_name=tool_name,
                            input=tool_input,
                            output="",
                            status="error",
                            error=error_msg,
                            duration_ms=duration_ms,
                        )
                    )
                    tool_results.append(
                        self._provider.format_tool_result(
                            tool_use_id=tool_use_id,
                            content=f"Error: {error_msg}",
                            is_error=True,
                        )
                    )

                # Snapshot for suspension
                ctx.suspended_tool_results = tool_results
                ctx.pending_tool_blocks = list(ctx.pending_tool_blocks[i + 1 :])
                return (TransportState.SUSPENDED, events)

            # Normal (non-suspending) tool execution
            start = time.monotonic_ns()
            try:
                result_text = await tool.execute(tool_input)
                duration_ms = (time.monotonic_ns() - start) // 1_000_000
                events.append(
                    ToolUse(
                        tool_name=tool_name,
                        input=tool_input,
                        output=result_text,
                        status="success",
                        duration_ms=duration_ms,
                    )
                )
                tool_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=result_text,
                        is_error=False,
                    )
                )
            except ToolError as e:
                duration_ms = (time.monotonic_ns() - start) // 1_000_000
                error_msg = str(e)
                events.append(
                    ToolUse(
                        tool_name=tool_name,
                        input=tool_input,
                        output="",
                        status="error",
                        error=error_msg,
                        duration_ms=duration_ms,
                    )
                )
                tool_results.append(
                    self._provider.format_tool_result(
                        tool_use_id=tool_use_id,
                        content=f"Error: {error_msg}",
                        is_error=True,
                    )
                )

        ctx.history.append({"role": "user", "content": tool_results})
        ctx.pending_tool_blocks = []
        return (TransportState.CALLING_API, events)

    async def _send_with_retry(
        self,
        *,
        url: str,
        headers: dict[str, str],
        json_body: dict[str, Any],
    ) -> tuple[httpx.Response | None, list[Event]]:
        events: list[Event] = []
        last_error: str = ""
        last_status: int = 0

        for attempt in range(self._retry.max_retries + 1):
            try:
                async with httpx.AsyncClient() as client:
                    response = await client.post(
                        url,
                        headers=headers,
                        json=json_body,
                        timeout=120.0,
                    )

                if response.status_code == 200:  # noqa: PLR2004
                    return response, events

                last_status = response.status_code
                last_error = response.text

                if response.status_code not in _TRANSIENT_STATUS_CODES:
                    events.append(
                        Error(
                            name="api_error",
                            message=f"HTTP {response.status_code}: {response.text}",
                            metadata={"status_code": response.status_code},
                        )
                    )
                    return None, events

                if attempt < self._retry.max_retries:
                    delay = min(
                        self._retry.backoff_base_seconds * (2**attempt),
                        self._retry.backoff_max_seconds,
                    )
                    if self._retry.jitter:
                        delay *= random.random()  # noqa: S311
                    events.append(
                        ApiRetry(
                            attempt=attempt + 1,
                            max_retries=self._retry.max_retries,
                            delay_ms=int(delay * 1000),
                            status_code=response.status_code,
                            error=response.text,
                        )
                    )
                    await asyncio.sleep(delay)

            except httpx.HTTPError as e:
                last_error = str(e)
                last_status = 0
                if attempt < self._retry.max_retries:
                    delay = min(
                        self._retry.backoff_base_seconds * (2**attempt),
                        self._retry.backoff_max_seconds,
                    )
                    if self._retry.jitter:
                        delay *= random.random()  # noqa: S311
                    events.append(
                        ApiRetry(
                            attempt=attempt + 1,
                            max_retries=self._retry.max_retries,
                            delay_ms=int(delay * 1000),
                            status_code=0,
                            error=str(e),
                        )
                    )
                    await asyncio.sleep(delay)

        events.append(
            Error(
                name="max_retries_exceeded",
                message=(
                    f"Failed after {self._retry.max_retries + 1} attempts: {last_error}"
                ),
                metadata={"status_code": last_status},
            )
        )
        return None, events
