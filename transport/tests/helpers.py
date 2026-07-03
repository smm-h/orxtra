"""Shared mock providers for transport tests.

Extracted from test_transport.py so that sibling test modules
(test_rate_limit, test_tool_progress, test_history_compaction) can
import them without importing a test module.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.transport._events import (
    ContentBlock,
    StreamDelta,
    StreamToolUse,
    StreamUsage,
    Thinking,
    TransportEvent,
    Usage,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


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
    ) -> AsyncIterator[TransportEvent]:
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
