from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.transport._events import StreamDelta
from orxtra.transport.providers._openai import OpenAIProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


@pytest.fixture
def provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key")


class TestBuildRequest:
    def test_basic_request(self, provider: OpenAIProvider) -> None:
        result = provider.build_request(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            system="You are helpful.",
            model="gpt-4o",
        )
        assert result["url"] == "https://api.openai.com/v1/chat/completions"
        assert result["headers"]["Authorization"] == "Bearer test-key"
        body = result["json_body"]
        assert body["model"] == "gpt-4o"
        # System prompt prepended as first message
        assert body["messages"][0] == {"role": "system", "content": "You are helpful."}
        assert body["messages"][1] == {"role": "user", "content": "Hello"}
        assert body["stream"] is True
        assert "tools" not in body

    def test_with_tools(self, provider: OpenAIProvider) -> None:
        tools = [
            {
                "name": "read_file",
                "description": "Read a file",
                "parameters": {"type": "object"},
            },
        ]
        result = provider.build_request(
            messages=[{"role": "user", "content": "Read it"}],
            tools=tools,
            system="sys",
            model="gpt-4o",
        )
        expected_tools = [
            {
                "type": "function",
                "function": {
                    "name": "read_file",
                    "description": "Read a file",
                    "parameters": {"type": "object"},
                },
            },
        ]
        assert result["json_body"]["tools"] == expected_tools

    def test_deferred_tools_get_compact_spec(self, provider: OpenAIProvider) -> None:
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
            model="gpt-4o",
        )
        formatted = result["json_body"]["tools"]
        assert len(formatted) == 2

        # Deferred tool: empty parameters, hint in description
        deferred_fn = formatted[0]["function"]
        assert deferred_fn["name"] == "read_file"
        assert deferred_fn["parameters"] == {"type": "object", "properties": {}}
        assert "load_tools" in deferred_fn["description"]

        # Non-deferred tool: full spec preserved
        normal_fn = formatted[1]["function"]
        assert normal_fn["name"] == "write_file"
        assert normal_fn["parameters"] == {"type": "object"}
        assert "load_tools" not in normal_fn["description"]

    def test_custom_endpoint(self) -> None:
        custom = OpenAIProvider(api_key="k", base_url="https://my-azure.openai.com")
        result = custom.build_request(
            messages=[],
            tools=[],
            system="s",
            model="m",
        )
        assert result["url"] == "https://my-azure.openai.com/chat/completions"


class TestParseResponse:
    def test_text_response(self, provider: OpenAIProvider) -> None:
        response = {
            "choices": [
                {"message": {"content": "Hello world", "role": "assistant"}},
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].text == "Hello world"

    def test_tool_call_response(self, provider: OpenAIProvider) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_abc",
                                "type": "function",
                                "function": {
                                    "name": "read_file",
                                    "arguments": '{"path": "/home/user/test"}',
                                },
                            },
                        ],
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "tool_use"
        assert blocks[0].tool_use_id == "call_abc"
        assert blocks[0].tool_name == "read_file"
        assert blocks[0].tool_input == {"path": "/home/user/test"}

    def test_text_and_tool_calls(self, provider: OpenAIProvider) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": "Let me help",
                        "role": "assistant",
                        "tool_calls": [
                            {
                                "id": "call_1",
                                "type": "function",
                                "function": {
                                    "name": "t1",
                                    "arguments": "{}",
                                },
                            },
                            {
                                "id": "call_2",
                                "type": "function",
                                "function": {
                                    "name": "t2",
                                    "arguments": '{"x": 1}',
                                },
                            },
                        ],
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 3
        assert blocks[0].type == "text"
        assert blocks[0].text == "Let me help"
        assert blocks[1].type == "tool_use"
        assert blocks[1].tool_name == "t1"
        assert blocks[2].type == "tool_use"
        assert blocks[2].tool_name == "t2"
        assert blocks[2].tool_input == {"x": 1}

    def test_tool_results_use_role_tool(self, provider: OpenAIProvider) -> None:
        response = {
            "choices": [
                {
                    "message": {
                        "content": "I got the file contents",
                        "role": "assistant",
                    },
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "text"


class TestExtractUsage:
    def test_basic_usage(self, provider: OpenAIProvider) -> None:
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
            },
        }
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0

    def test_full_usage(self, provider: OpenAIProvider) -> None:
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "completion_tokens_details": {"reasoning_tokens": 25},
                "prompt_tokens_details": {"cached_tokens": 10},
            },
        }
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 25
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 0

    def test_missing_usage(self, provider: OpenAIProvider) -> None:
        response: dict[str, object] = {}
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 0
        assert usage.output_tokens == 0

    def test_null_details(self, provider: OpenAIProvider) -> None:
        response = {
            "usage": {
                "prompt_tokens": 100,
                "completion_tokens": 50,
                "completion_tokens_details": None,
                "prompt_tokens_details": None,
            },
        }
        usage = provider.extract_usage(response)
        assert usage.reasoning_tokens == 0
        assert usage.cache_read_tokens == 0


class TestParseStream:
    @staticmethod
    async def _bytes_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
        for chunk in chunks:
            yield chunk

    async def test_text_delta(self, provider: OpenAIProvider) -> None:
        chunks = [
            b'data: {"choices": [{"delta": {"content": "Hello"}}]}\n\n',
            b'data: {"choices": [{"delta": {"content": " world"}}]}\n\n',
            b"data: [DONE]\n\n",
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

    async def test_stops_on_done(self, provider: OpenAIProvider) -> None:
        chunks = [
            b'data: {"choices": [{"delta": {"content": "a"}}]}\n\n',
            b"data: [DONE]\n\n",
            b'data: {"choices": [{"delta": {"content": "b"}}]}\n\n',
        ]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert events[0].text == "a"

    async def test_empty_delta(self, provider: OpenAIProvider) -> None:
        chunks = [
            b'data: {"choices": [{"delta": {}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in provider.parse_stream(
                TestParseStream._bytes_iter(chunks),
            )
        ]
        assert len(events) == 0


class TestWrapToolResults:
    def test_returns_results_as_separate_messages(self) -> None:
        provider = OpenAIProvider(api_key="test-key")
        results = [
            {"role": "tool", "tool_call_id": "tc_1", "content": "result 1"},
            {"role": "tool", "tool_call_id": "tc_2", "content": "result 2"},
        ]
        wrapped = provider.wrap_tool_results(results)
        assert wrapped == results
        assert len(wrapped) == 2
        assert wrapped[0]["role"] == "tool"
        assert wrapped[1]["role"] == "tool"
