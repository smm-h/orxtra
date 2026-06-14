from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from orxt.transport._events import ContentBlock, StreamDelta, Thinking, Usage

if TYPE_CHECKING:
    from orxt.transport._events import Event


@dataclass(frozen=True)
class AnthropicProvider:
    api_key: str = field(default_factory=lambda: os.environ["ANTHROPIC_API_KEY"])
    base_url: str = "https://api.anthropic.com"
    api_version: str = "2023-06-01"

    def build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        model: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "max_tokens": 16384,
            "system": system,
            "messages": messages,
            "stream": True,
        }
        if tools:
            body["tools"] = tools
        return {
            "url": f"{self.base_url}/v1/messages",
            "headers": {
                "x-api-key": self.api_key,
                "anthropic-version": self.api_version,
                "content-type": "application/json",
            },
            "json_body": body,
        }

    def parse_response(self, response: dict[str, Any]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        for item in response["content"]:
            block_type: str = item["type"]
            if block_type == "text":
                blocks.append(ContentBlock(type="text", text=item["text"]))
            elif block_type == "tool_use":
                blocks.append(
                    ContentBlock(
                        type="tool_use",
                        tool_use_id=item["id"],
                        tool_name=item["name"],
                        tool_input=item["input"],
                    ),
                )
            elif block_type == "thinking":
                blocks.append(ContentBlock(type="thinking", text=item["thinking"]))
        return blocks

    async def parse_stream(
        self,
        byte_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[Event]:
        buffer = b""
        event_type = ""
        async for chunk in byte_stream:
            buffer += chunk
            while b"\n" in buffer:
                line_bytes, buffer = buffer.split(b"\n", 1)
                line = line_bytes.decode("utf-8").rstrip("\r")
                if line.startswith("event: "):
                    event_type = line[7:]
                elif line.startswith("data: "):
                    data: dict[str, Any] = json.loads(line[6:])
                    if event_type == "content_block_delta":
                        delta: dict[str, Any] = data["delta"]
                        delta_type: str = delta["type"]
                        if delta_type == "text_delta":
                            yield StreamDelta(text=delta["text"])
                        elif delta_type == "thinking_delta":
                            yield Thinking(text=delta["thinking"])
                    elif event_type == "message_stop":
                        return

    def extract_usage(self, response: dict[str, Any]) -> Usage:
        usage: dict[str, Any] = response["usage"]
        return Usage(
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            reasoning_tokens=usage.get("reasoning_tokens", 0),
            cache_read_tokens=usage.get("cache_read_input_tokens", 0),
            cache_write_tokens=usage.get("cache_creation_input_tokens", 0),
        )

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
