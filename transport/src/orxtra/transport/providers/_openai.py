from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from orxtra.transport._events import ContentBlock, StreamDelta, StreamToolUse, Usage

if TYPE_CHECKING:
    from orxtra.transport._events import Event


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

    async def parse_stream(  # noqa: C901, PLR0912
        self,
        byte_stream: AsyncIterator[bytes],
    ) -> AsyncIterator[Event]:
        buffer = ""
        # Tool call accumulation state
        tool_calls: dict[int, dict[str, str]] = {}

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
                    # Yield any pending tool calls
                    for _idx, tc in sorted(tool_calls.items()):
                        try:
                            tool_input = json.loads(
                                tc.get("arguments", "{}"),
                            )
                        except json.JSONDecodeError:
                            tool_input = {}
                        yield StreamToolUse(
                            tool_use_id=tc.get("id", ""),
                            tool_name=tc.get("name", ""),
                            tool_input=tool_input,
                        )
                    return
                parsed = json.loads(data)
                choices = parsed.get("choices", [])
                if not choices:
                    continue
                delta = choices[0].get("delta", {})
                text = delta.get("content")
                if text:
                    yield StreamDelta(text=text)
                # Handle tool calls
                for tc_delta in delta.get("tool_calls", []):
                    idx = tc_delta.get("index", 0)
                    if idx not in tool_calls:
                        tool_calls[idx] = {
                            "id": "", "name": "", "arguments": "",
                        }
                    if "id" in tc_delta:
                        tool_calls[idx]["id"] = tc_delta["id"]
                    func = tc_delta.get("function", {})
                    if "name" in func:
                        tool_calls[idx]["name"] = func["name"]
                    if "arguments" in func:
                        tool_calls[idx]["arguments"] += func["arguments"]

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

    def format_tool_result(
        self, tool_use_id: str, content: str, is_error: bool,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {
            "role": "tool",
            "tool_call_id": tool_use_id,
            "content": content,
        }

    def wrap_tool_results(
        self, results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return results

    def format_assistant_message(
        self, blocks: list[ContentBlock],
    ) -> dict[str, Any]:
        message: dict[str, Any] = {"role": "assistant"}
        text_parts = [b.text for b in blocks if b.type == "text" and b.text is not None]
        if text_parts:
            message["content"] = " ".join(text_parts)
        else:
            message["content"] = None
        tool_calls = [
            {
                "id": b.tool_use_id,
                "type": "function",
                "function": {
                    "name": b.tool_name,
                    "arguments": json.dumps(b.tool_input),
                },
            }
            for b in blocks
            if b.type == "tool_use"
        ]
        if tool_calls:
            message["tool_calls"] = tool_calls
        return message
