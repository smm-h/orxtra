from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.transport._events import StreamDelta, StreamToolUse, StreamUsage
from orxtra.transport.providers._google import GoogleProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def provider() -> GoogleProvider:
    return GoogleProvider(api_key="test-key")


class TestBuildRequest:
    def test_simple(self, provider: GoogleProvider) -> None:
        result = provider.build_request(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            system="",
            model="gemini-2.5-flash",
        )
        assert result["url"] == (
            "https://generativelanguage.googleapis.com/v1beta"
            "/models/gemini-2.5-flash:streamGenerateContent?alt=sse"
        )
        assert result["headers"]["x-goog-api-key"] == "test-key"
        body = result["json_body"]
        assert body["contents"] == [
            {"role": "user", "parts": [{"text": "Hello"}]},
        ]
        assert "system_instruction" not in body
        assert "tools" not in body

    def test_with_tools(self, provider: GoogleProvider) -> None:
        tools = [
            {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                    },
                    "required": ["path"],
                },
            },
        ]
        result = provider.build_request(
            messages=[{"role": "user", "content": "Read it"}],
            tools=tools,
            system="sys",
            model="gemini-2.5-flash",
        )
        body = result["json_body"]
        assert len(body["tools"]) == 1
        decls = body["tools"][0]["functionDeclarations"]
        assert len(decls) == 1
        assert decls[0]["name"] == "read_file"
        assert decls[0]["description"] == "Read a file"
        assert decls[0]["parameters"]["type"] == "object"

    def test_deferred_tools_omit_parameters(self, provider: GoogleProvider) -> None:
        tools = [
            {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                "deferred": True,
            },
            {
                "name": "write_file",
                "description": "Write a file",
                "parameters": {"type": "object"},
            },
        ]
        result = provider.build_request(
            messages=[{"role": "user", "content": "hi"}],
            tools=tools,
            system="sys",
            model="gemini-2.5-flash",
        )
        decls = result["json_body"]["tools"][0]["functionDeclarations"]
        assert len(decls) == 2

        # Deferred tool: no parameters, hint in description
        assert decls[0]["name"] == "read_file"
        assert "parameters" not in decls[0]
        assert "load_tools" in decls[0]["description"]

        # Non-deferred tool: parameters preserved
        assert decls[1]["name"] == "write_file"
        assert "parameters" in decls[1]
        assert "load_tools" not in decls[1]["description"]

    def test_with_system(self, provider: GoogleProvider) -> None:
        result = provider.build_request(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="You are helpful.",
            model="gemini-2.5-flash",
        )
        body = result["json_body"]
        assert body["system_instruction"] == {
            "parts": [{"text": "You are helpful."}],
        }

    def test_message_role_mapping(self, provider: GoogleProvider) -> None:
        result = provider.build_request(
            messages=[
                {"role": "user", "content": "Hello"},
                {"role": "assistant", "content": "Hi there"},
                {"role": "user", "content": "How are you?"},
            ],
            tools=[],
            system="",
            model="gemini-2.5-flash",
        )
        contents = result["json_body"]["contents"]
        assert contents[0]["role"] == "user"
        assert contents[1]["role"] == "model"
        assert contents[2]["role"] == "user"

    def test_custom_base_url(self) -> None:
        custom = GoogleProvider(
            api_key="k",
            base_url="https://custom.example.com/v1",
        )
        result = custom.build_request(
            messages=[],
            tools=[],
            system="",
            model="m",
        )
        assert result["url"].startswith("https://custom.example.com/v1/")


class TestParseResponse:
    def test_text(self, provider: GoogleProvider) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [{"text": "Hello world"}],
                        "role": "model",
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].text == "Hello world"

    def test_function_call(self, provider: GoogleProvider) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "name": "read_file",
                                    "args": {"path": "/tmp/test"},
                                },
                            },
                        ],
                        "role": "model",
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "tool_use"
        assert blocks[0].tool_name == "read_file"
        assert blocks[0].tool_input == {"path": "/tmp/test"}

    def test_function_call_with_id(self, provider: GoogleProvider) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {
                                "functionCall": {
                                    "id": "call_123",
                                    "name": "read_file",
                                    "args": {"path": "/tmp/test"},
                                },
                            },
                        ],
                        "role": "model",
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert blocks[0].tool_use_id == "call_123"

    def test_empty_candidates(self, provider: GoogleProvider) -> None:
        response: dict[str, object] = {"candidates": []}
        blocks = provider.parse_response(response)
        assert blocks == []

    def test_text_and_function_call(self, provider: GoogleProvider) -> None:
        response = {
            "candidates": [
                {
                    "content": {
                        "parts": [
                            {"text": "Let me help"},
                            {
                                "functionCall": {
                                    "name": "search",
                                    "args": {"q": "test"},
                                },
                            },
                        ],
                        "role": "model",
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 2
        assert blocks[0].type == "text"
        assert blocks[0].text == "Let me help"
        assert blocks[1].type == "tool_use"
        assert blocks[1].tool_name == "search"


class TestExtractUsage:
    def test_basic_usage(self, provider: GoogleProvider) -> None:
        response = {
            "usageMetadata": {
                "promptTokenCount": 100,
                "candidatesTokenCount": 50,
                "totalTokenCount": 150,
            },
        }
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50

    def test_missing_usage(self, provider: GoogleProvider) -> None:
        response: dict[str, object] = {}
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0


class TestFormatToolResult:
    def test_basic(self, provider: GoogleProvider) -> None:
        result = provider.format_tool_result(
            tool_use_id="read_file",
            content="file contents here",
            is_error=False,
        )
        assert result == {
            "functionResponse": {
                "name": "read_file",
                "response": {"result": "file contents here"},
            },
        }

    def test_error_ignored(self, provider: GoogleProvider) -> None:
        # Gemini functionResponse has no is_error field; it's ignored
        result = provider.format_tool_result(
            tool_use_id="read_file",
            content="not found",
            is_error=True,
        )
        assert result["functionResponse"]["response"]["result"] == "not found"


class TestWrapToolResults:
    def test_wraps_in_single_user_message(
        self,
        provider: GoogleProvider,
    ) -> None:
        results = [
            {
                "functionResponse": {
                    "name": "read_file",
                    "response": {"result": "content1"},
                },
            },
            {
                "functionResponse": {
                    "name": "write_file",
                    "response": {"result": "ok"},
                },
            },
        ]
        wrapped = provider.wrap_tool_results(results)
        assert len(wrapped) == 1
        assert wrapped[0]["role"] == "user"
        assert wrapped[0]["parts"] == results


class TestFormatAssistantMessage:
    def test_text_message(self, provider: GoogleProvider) -> None:
        from orxtra.transport._events import ContentBlock

        blocks = [ContentBlock(type="text", text="Hello")]
        msg = provider.format_assistant_message(blocks)
        assert msg["role"] == "model"
        assert msg["parts"] == [{"text": "Hello"}]

    def test_tool_use_message(self, provider: GoogleProvider) -> None:
        from orxtra.transport._events import ContentBlock

        blocks = [
            ContentBlock(
                type="tool_use",
                tool_use_id="call_1",
                tool_name="read_file",
                tool_input={"path": "/tmp"},
            ),
        ]
        msg = provider.format_assistant_message(blocks)
        assert msg["role"] == "model"
        assert len(msg["parts"]) == 1
        fc = msg["parts"][0]["functionCall"]
        assert fc["name"] == "read_file"
        assert fc["args"] == {"path": "/tmp"}
        assert fc["id"] == "call_1"


class TestParseStream:
    @staticmethod
    async def _bytes_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    async def test_text_delta(self, provider: GoogleProvider) -> None:
        chunks = [
            b'data: {"candidates": [{"content": {"parts": [{"text": "Hello"}]}}]}\n\n',
            b'data: {"candidates": [{"content": {"parts": [{"text": " world"}]}}]}\n\n',
        ]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        assert len(events) == 2
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "Hello"
        assert isinstance(events[1], StreamDelta)
        assert events[1].text == " world"

    async def test_function_call_stream(
        self,
        provider: GoogleProvider,
    ) -> None:
        chunks = [
            b'data: {"candidates": [{"content": {"parts": [{"functionCall": {"name": "search", "args": {"q": "test"}}}]}}]}\n\n',
        ]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamToolUse)
        assert events[0].tool_name == "search"
        assert events[0].tool_input == {"q": "test"}

    async def test_usage_metadata_in_stream(
        self,
        provider: GoogleProvider,
    ) -> None:
        chunks = [
            b'data: {"candidates": [{"content": {"parts": [{"text": "hi"}]}}], "usageMetadata": {"promptTokenCount": 10, "candidatesTokenCount": 5}}\n\n',
        ]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        # text delta + usage event
        assert len(events) == 2
        assert isinstance(events[0], StreamDelta)
        assert isinstance(events[1], StreamUsage)
        assert events[1].usage.input_tokens == 10
        assert events[1].usage.output_tokens == 5

    async def test_empty_stream(self, provider: GoogleProvider) -> None:
        chunks = [b"\n\n"]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        assert events == []

    async def test_sse_comment_ignored(
        self,
        provider: GoogleProvider,
    ) -> None:
        chunks = [
            b": keep-alive\n",
            b'data: {"candidates": [{"content": {"parts": [{"text": "ok"}]}}]}\n\n',
        ]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "ok"
