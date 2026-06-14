from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from orxt.transport._events import ContentBlock, StreamDelta, Usage

if TYPE_CHECKING:
    from orxt.transport._events import Event


@dataclass(frozen=True)
class OpenAIProvider:
    api_key: str = field(default_factory=lambda: os.environ["OPENAI_API_KEY"])
    base_url: str = "https://api.openai.com/v1"

    def build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        model: str,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": [{"role": "system", "content": system}, *messages],
            "stream": True,
        }
        if tools:
            body["tools"] = [
                {"type": "function", "function": t} for t in tools
            ]
        return {
            "url": f"{self.base_url}/chat/completions",
            "headers": {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            "json_body": body,
        }

    def parse_response(self, response: dict[str, Any]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        message = response["choices"][0]["message"]

        content = message.get("content")
        if content is not None:
            blocks.append(ContentBlock(type="text", text=content))

        blocks.extend(
            ContentBlock(
                type="tool_use",
                tool_use_id=tc["id"],
                tool_name=tc["function"]["name"],
                tool_input=json.loads(tc["function"]["arguments"]),
            )
            for tc in message.get("tool_calls", [])
        )

        return blocks

    async def parse_stream(
        self,
        byte_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[Event]:
        buffer = ""
        async for chunk in byte_stream:
            buffer += chunk.decode("utf-8")
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                line = line.strip()
                if not line or line.startswith(":"):
                    continue
                if not line.startswith("data: "):
                    continue
                data = line[len("data: "):]
                if data == "[DONE]":
                    return
                parsed = json.loads(data)
                choices = parsed.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield StreamDelta(text=text)

    def extract_usage(self, response: dict[str, Any]) -> Usage:
        usage = response.get("usage", {})
        completion_details = usage.get("completion_tokens_details", {}) or {}
        prompt_details = usage.get("prompt_tokens_details", {}) or {}
        return Usage(
            input_tokens=usage.get("prompt_tokens", 0),
            output_tokens=usage.get("completion_tokens", 0),
            reasoning_tokens=completion_details.get("reasoning_tokens", 0),
            cache_read_tokens=prompt_details.get("cached_tokens", 0),
            cache_write_tokens=0,
        )
