from __future__ import annotations

import pytest
from orxt.transport._events import StreamDelta, Thinking
from orxt.transport.providers._anthropic import AnthropicProvider


@pytest.fixture
def provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="test-key")


class TestBuildRequest:
    def test_basic_request(self, provider: AnthropicProvider) -> None:
        result = provider.build_request(
            messages=[{"role": "user", "content": "Hello"}],
            tools=[],
            system="You are helpful.",
            model="claude-sonnet-4-20250514",
        )
        assert result["url"] == "https://api.anthropic.com/v1/messages"
        assert result["headers"]["x-api-key"] == "test-key"
        assert result["headers"]["anthropic-version"] == "2023-06-01"
        body = result["json_body"]
        assert body["model"] == "claude-sonnet-4-20250514"
        assert body["system"] == "You are helpful."
        assert body["messages"] == [{"role": "user", "content": "Hello"}]
        assert body["stream"] is True
        assert "tools" not in body

    def test_with_tools(self, provider: AnthropicProvider) -> None:
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
            model="claude-sonnet-4-20250514",
        )
        assert result["json_body"]["tools"] == tools

    def test_system_prompt_location(
        self,
        provider: AnthropicProvider,
    ) -> None:
        result = provider.build_request(
            messages=[{"role": "user", "content": "hi"}],
            tools=[],
            system="I am system",
            model="claude-sonnet-4-20250514",
        )
        # System prompt is a top-level parameter, not in messages
        assert result["json_body"]["system"] == "I am system"
        for msg in result["json_body"]["messages"]:
            assert msg["role"] != "system"


class TestParseResponse:
    def test_text_block(self, provider: AnthropicProvider) -> None:
        response = {
            "content": [
                {"type": "text", "text": "Hello world"},
            ],
            "usage": {"input_tokens": 10, "output_tokens": 5},
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "text"
        assert blocks[0].text == "Hello world"

    def test_tool_use_block(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response = {
            "content": [
                {
                    "type": "tool_use",
                    "id": "tu_abc",
                    "name": "read_file",
                    "input": {"path": "/home/user/test"},
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "tool_use"
        assert blocks[0].tool_use_id == "tu_abc"
        assert blocks[0].tool_name == "read_file"
        assert blocks[0].tool_input == {"path": "/home/user/test"}

    def test_thinking_block(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response = {
            "content": [
                {"type": "thinking", "thinking": "Let me consider..."},
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 1
        assert blocks[0].type == "thinking"
        assert blocks[0].text == "Let me consider..."

    def test_mixed_blocks(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response = {
            "content": [
                {"type": "thinking", "thinking": "hmm"},
                {"type": "text", "text": "result"},
                {
                    "type": "tool_use",
                    "id": "tu_1",
                    "name": "t",
                    "input": {},
                },
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 3
        assert blocks[0].type == "thinking"
        assert blocks[1].type == "text"
        assert blocks[2].type == "tool_use"


class TestExtractUsage:
    def test_basic_usage(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
            },
        }
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 0
        assert usage.cache_read_tokens == 0
        assert usage.cache_write_tokens == 0

    def test_full_usage(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response = {
            "usage": {
                "input_tokens": 100,
                "output_tokens": 50,
                "reasoning_tokens": 25,
                "cache_read_input_tokens": 10,
                "cache_creation_input_tokens": 5,
            },
        }
        usage = provider.extract_usage(response)
        assert usage.input_tokens == 100
        assert usage.output_tokens == 50
        assert usage.reasoning_tokens == 25
        assert usage.cache_read_tokens == 10
        assert usage.cache_write_tokens == 5


def _sse_chunk(event: str, data: str) -> bytes:
    return f"event: {event}\ndata: {data}\n\n".encode()


def _text_delta(text: str) -> bytes:
    return _sse_chunk(
        "content_block_delta",
        f'{{"delta": {{"type": "text_delta", "text": "{text}"}}}}',
    )


def _thinking_delta(thinking: str) -> bytes:
    return _sse_chunk(
        "content_block_delta",
        f'{{"delta": {{"type": "thinking_delta",'
        f' "thinking": "{thinking}"}}}}',
    )


_MESSAGE_STOP = _sse_chunk("message_stop", "{}")


async def _bytes_iter(chunks: list[bytes]):  # noqa: ANN202
    for chunk in chunks:
        yield chunk


class TestParseStream:
    async def test_text_delta(
        self,
        provider: AnthropicProvider,
    ) -> None:
        chunks = [
            _text_delta("Hello"),
            _text_delta(" world"),
            _MESSAGE_STOP,
        ]
        events = [
            event
            async for event in provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 2
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "Hello"
        assert isinstance(events[1], StreamDelta)
        assert events[1].text == " world"

    async def test_thinking_delta(
        self,
        provider: AnthropicProvider,
    ) -> None:
        chunks = [
            _thinking_delta("Let me think"),
            _MESSAGE_STOP,
        ]
        events = [
            event
            async for event in provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], Thinking)
        assert events[0].text == "Let me think"

    async def test_stops_on_message_stop(
        self,
        provider: AnthropicProvider,
    ) -> None:
        chunks = [
            _text_delta("a"),
            _MESSAGE_STOP,
            _text_delta("b"),
        ]
        events = [
            event
            async for event in provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 1


class TestWrapToolResults:
    def test_wraps_in_single_user_message(self) -> None:
        provider = AnthropicProvider(api_key="test-key")
        results = [
            {"type": "tool_result", "tool_use_id": "tu_1", "content": "result 1"},
            {"type": "tool_result", "tool_use_id": "tu_2", "content": "result 2"},
        ]
        wrapped = provider.wrap_tool_results(results)
        assert len(wrapped) == 1
        assert wrapped[0]["role"] == "user"
        assert wrapped[0]["content"] == results
