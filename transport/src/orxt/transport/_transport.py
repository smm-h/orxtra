from __future__ import annotations

import asyncio
import random
import time
from typing import TYPE_CHECKING, Any

import httpx
import uuid6

from ._events import (
    ApiRetry,
    ContentBlock,
    Error,
    Event,
    Result,
    StepFinish,
    StepStart,
    StreamDelta,
    Text,
    Thinking,
    ToolUse,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from ._provider import Provider, RetryPolicy

from orxt.protocols import Tool, ToolError

_TRANSIENT_STATUS_CODES = frozenset({429, 500, 502, 503})


def _validate_tool_args(args: dict[str, Any], schema: dict[str, Any]) -> str | None:
    required = schema.get("required", [])
    for field in required:
        if field not in args:
            return f"Missing required field: {field}"
    properties = schema.get("properties", {})
    if not schema.get("additionalProperties", True):
        for key in args:
            if key not in properties:
                return f"Unknown field: {key}"
    return None


class Transport:
    def __init__(self, provider: Provider, retry_policy: RetryPolicy) -> None:
        self._provider = provider
        self._retry = retry_policy
        self._sessions: dict[str, list[dict[str, Any]]] = {}

    async def send(  # noqa: C901, PLR0912, PLR0913, PLR0915
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

        total_input = 0
        total_output = 0
        total_reasoning = 0
        total_cache_read = 0
        total_cache_write = 0
        total_tool_calls = 0

        while True:
            request = self._provider.build_request(
                messages=history,
                tools=tool_specs,
                system=system_prompt,
                model=model,
            )

            json_body = request["json_body"]
            json_body["stream"] = False

            response, retry_events = await self._send_with_retry(
                url=request["url"],
                headers=request["headers"],
                json_body=json_body,
            )
            for event in retry_events:
                yield event
            if response is None:
                break

            response_data = response.json()
            blocks = self._provider.parse_response(response_data)
            usage = self._provider.extract_usage(response_data)

            total_input += usage.input_tokens
            total_output += usage.output_tokens
            total_reasoning += usage.reasoning_tokens
            total_cache_read += usage.cache_read_tokens
            total_cache_write += usage.cache_write_tokens

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
                    yield Thinking(text=block.text)

            for block in text_blocks:
                if block.text is not None:
                    if stream_deltas:
                        yield StreamDelta(text=block.text)
                    yield Text(text=block.text)

            if tool_use_blocks:
                history.append(self._provider.format_assistant_message(blocks))

                tool_results: list[dict[str, Any]] = []

                for block in tool_use_blocks:
                    total_tool_calls += 1
                    tool_name = block.tool_name or ""
                    tool_input = block.tool_input or {}
                    tool_use_id = block.tool_use_id or ""

                    tool = tool_map.get(tool_name)
                    if tool is None:
                        yield ToolUse(
                            tool_name=tool_name,
                            input=tool_input,
                            output="",
                            status="error",
                            error=f"Unknown tool: {tool_name}",
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
                        yield ToolUse(
                            tool_name=tool_name,
                            input=tool_input,
                            output="",
                            status="error",
                            error=validation_error,
                        )
                        tool_results.append(
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
                        yield ToolUse(
                            tool_name=tool_name,
                            input=tool_input,
                            output="",
                            status="error",
                            error=error_msg,
                            duration_ms=duration_ms,
                        )
                        tool_results.append(
                            self._provider.format_tool_result(
                                tool_use_id=tool_use_id,
                                content=f"Error: {error_msg}",
                                is_error=True,
                            )
                        )

                history.append({"role": "user", "content": tool_results})
                continue

            full_text = " ".join(
                block.text for block in text_blocks if block.text is not None
            )
            history.append(self._provider.format_assistant_message(blocks))

            yield StepFinish(
                reason="end_turn",
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                reasoning_tokens=usage.reasoning_tokens,
                cache_read_tokens=usage.cache_read_tokens,
                cache_write_tokens=usage.cache_write_tokens,
            )
            yield Result(
                text=full_text,
                session_id=session_id,
                total_input_tokens=total_input,
                total_output_tokens=total_output,
                total_reasoning_tokens=total_reasoning,
                total_cache_read_tokens=total_cache_read,
                total_cache_write_tokens=total_cache_write,
                tool_calls=total_tool_calls,
            )
            break

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
