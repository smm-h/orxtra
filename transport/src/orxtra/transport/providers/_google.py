from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

from orxtra.transport._events import (
    ContentBlock,
    StreamDelta,
    StreamToolUse,
    StreamUsage,
    UnknownEvent,
    Usage,
)

if TYPE_CHECKING:
    from orxtra.transport._events import Event


@dataclass(frozen=True)
class GoogleProvider:
    api_key: str = field(default_factory=lambda: os.environ["GOOGLE_API_KEY"])
    base_url: str = "https://generativelanguage.googleapis.com/v1beta"

    # -- request building ------------------------------------------------

    def build_request(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        system: str,
        model: str,
    ) -> dict[str, Any]:
        contents = _convert_messages(messages)
        body: dict[str, Any] = {"contents": contents}
        if system:
            body["system_instruction"] = {"parts": [{"text": system}]}
        if tools:
            body["tools"] = [
                {
                    "functionDeclarations": [
                        _convert_tool(t) for t in tools
                    ],
                },
            ]
        return {
            "url": (
                f"{self.base_url}/models/{model}"
                ":streamGenerateContent?alt=sse"
            ),
            "headers": {
                "x-goog-api-key": self.api_key,
                "Content-Type": "application/json",
            },
            "json_body": body,
        }

    # -- response parsing ------------------------------------------------

    def parse_response(self, response: dict[str, Any]) -> list[ContentBlock]:
        blocks: list[ContentBlock] = []
        candidates = response.get("candidates", [])
        if not candidates:
            return blocks
        parts = candidates[0].get("content", {}).get("parts", [])
        for part in parts:
            if "text" in part:
                blocks.append(ContentBlock(type="text", text=part["text"]))
            elif "functionCall" in part:
                fc = part["functionCall"]
                blocks.append(
                    ContentBlock(
                        type="tool_use",
                        tool_use_id=fc.get("id", fc["name"]),
                        tool_name=fc["name"],
                        tool_input=fc.get("args", {}),
                    ),
                )
            else:
                blocks.append(
                    ContentBlock(type="unknown", text=json.dumps(part)),
                )
        return blocks

    # -- streaming -------------------------------------------------------

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
                data_str = line[len("data: "):]
                try:
                    data: dict[str, Any] = json.loads(data_str)
                except json.JSONDecodeError:
                    continue
                candidates = data.get("candidates", [])
                if candidates:
                    parts = (
                        candidates[0]
                        .get("content", {})
                        .get("parts", [])
                    )
                    for part in parts:
                        if "text" in part:
                            yield StreamDelta(text=part["text"])
                        elif "functionCall" in part:
                            fc = part["functionCall"]
                            yield StreamToolUse(
                                tool_use_id=fc.get("id", fc["name"]),
                                tool_name=fc["name"],
                                tool_input=fc.get("args", {}),
                            )
                        else:
                            yield UnknownEvent(raw=part)
                usage_meta = data.get("usageMetadata")
                if usage_meta:
                    yield StreamUsage(
                        usage=Usage(
                            input_tokens=usage_meta.get(
                                "promptTokenCount", 0,
                            ),
                            output_tokens=usage_meta.get(
                                "candidatesTokenCount", 0,
                            ),
                        ),
                    )

    # -- usage extraction ------------------------------------------------

    def extract_usage(self, response: dict[str, Any]) -> Usage:
        usage = response.get("usageMetadata", {})
        return Usage(
            input_tokens=usage.get("promptTokenCount", 0),
            output_tokens=usage.get("candidatesTokenCount", 0),
        )

    # -- tool results ----------------------------------------------------

    def format_tool_result(
        self, tool_use_id: str, content: str, is_error: bool,  # noqa: ARG002
    ) -> dict[str, Any]:
        return {
            "functionResponse": {
                "name": tool_use_id,
                "response": {"result": content},
            },
        }

    def wrap_tool_results(
        self, results: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        return [{"role": "user", "parts": results}]

    # -- assistant message formatting ------------------------------------

    def format_assistant_message(
        self, blocks: list[ContentBlock],
    ) -> dict[str, Any]:
        parts: list[dict[str, Any]] = []
        for b in blocks:
            if b.type == "text" and b.text is not None:
                parts.append({"text": b.text})
            elif b.type == "tool_use":
                fc: dict[str, Any] = {
                    "name": b.tool_name,
                    "args": b.tool_input or {},
                }
                if b.tool_use_id and b.tool_use_id != b.tool_name:
                    fc["id"] = b.tool_use_id
                parts.append({"functionCall": fc})
        return {"role": "model", "parts": parts}


# -- internal helpers ----------------------------------------------------


def _convert_messages(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert orxtra-internal messages to Gemini ``contents`` format."""
    contents: list[dict[str, Any]] = []
    for msg in messages:
        role = _map_role(msg.get("role", "user"))
        parts = _extract_parts(msg)
        contents.append({"role": role, "parts": parts})
    return contents


def _map_role(role: str) -> str:
    """Map standard roles to Gemini roles."""
    if role in ("assistant", "model"):
        return "model"
    return "user"


def _extract_parts(msg: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract parts from a message dict."""
    # Simple string content
    content = msg.get("content")
    if isinstance(content, str):
        return [{"text": content}]
    # List of blocks (Anthropic-style content blocks)
    if isinstance(content, list):
        parts: list[dict[str, Any]] = []
        for block in content:
            if isinstance(block, str):
                parts.append({"text": block})
            elif isinstance(block, dict):
                parts.extend(_convert_content_block(block))
        return parts
    # Parts already in Gemini format (passthrough)
    if "parts" in msg:
        return list(msg["parts"])
    return [{"text": ""}]


def _convert_content_block(block: dict[str, Any]) -> list[dict[str, Any]]:
    """Convert a single content block to Gemini parts."""
    block_type = block.get("type", "")
    if block_type == "text":
        return [{"text": block.get("text", "")}]
    if block_type == "tool_use":
        fc: dict[str, Any] = {
            "name": block.get("name", ""),
            "args": block.get("input", {}),
        }
        tool_id = block.get("id")
        if tool_id:
            fc["id"] = tool_id
        return [{"functionCall": fc}]
    if block_type == "tool_result":
        return [
            {
                "functionResponse": {
                    "name": block.get("tool_use_id", ""),
                    "response": {"result": block.get("content", "")},
                },
            },
        ]
    return [{"text": json.dumps(block)}]


def _convert_tool(tool: dict[str, Any]) -> dict[str, Any]:
    """Convert an orxtra tool definition to a Gemini functionDeclaration.

    Deferred tools omit ``parameters`` entirely (Gemini allows this),
    producing a compact declaration with only name and description.
    """
    decl: dict[str, Any] = {"name": tool["name"]}
    if "description" in tool:
        desc = tool["description"]
        if tool.get("deferred"):
            desc = (
                f"{desc} "
                "(Schema not loaded -- call load_tools to load full schema first.)"
            )
        decl["description"] = desc
    if not tool.get("deferred"):
        if "parameters" in tool:
            decl["parameters"] = tool["parameters"]
        elif "input_schema" in tool:
            decl["parameters"] = tool["input_schema"]
    return decl
