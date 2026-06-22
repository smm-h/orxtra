"""Tests for streaming edge cases in provider parse_stream methods."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from orxtra.transport._events import StreamDelta, StreamToolUse, Thinking
from orxtra.transport.providers._anthropic import AnthropicProvider
from orxtra.transport.providers._openai import OpenAIProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


async def _bytes_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _anthropic_sse(event_type: str, data: str) -> bytes:
    return f"event: {event_type}\ndata: {data}\n\n".encode()


@pytest.fixture
def anthropic_provider() -> AnthropicProvider:
    return AnthropicProvider(api_key="test-key")


@pytest.fixture
def openai_provider() -> OpenAIProvider:
    return OpenAIProvider(api_key="test-key")


class TestAnthropicStreamEdgeCases:
    """Edge cases for AnthropicProvider.parse_stream."""

    async def test_unknown_delta_type_ignored(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """input_json_delta (tool input streaming) is not text or thinking."""
        chunk = _anthropic_sse(
            "content_block_delta",
            json.dumps({"delta": {"type": "input_json_delta", "partial_json": '{"x":'}}),  # noqa: E501
        )
        stop = _anthropic_sse("message_stop", "{}")
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter([chunk, stop]),
            )
        ]
        assert events == []

    async def test_content_block_start_ignored(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """content_block_start is not content_block_delta or message_stop."""
        chunk = _anthropic_sse(
            "content_block_start",
            json.dumps({"index": 0, "content_block": {"type": "text", "text": ""}}),
        )
        stop = _anthropic_sse("message_stop", "{}")
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter([chunk, stop]),
            )
        ]
        assert events == []

    async def test_chunked_data_across_byte_boundaries(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """A single SSE event split across two byte chunks."""
        full = _anthropic_sse(
            "content_block_delta",
            json.dumps({"delta": {"type": "text_delta", "text": "split"}}),
        )
        # Split in the middle of the data line
        mid = len(full) // 2
        chunk_a = full[:mid]
        chunk_b = full[mid:]
        stop = _anthropic_sse("message_stop", "{}")
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter([chunk_a, chunk_b, stop]),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "split"

    async def test_empty_stream(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """No chunks at all -- should yield nothing."""
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter([]),
            )
        ]
        assert events == []

    async def test_multiple_events_in_single_chunk(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """Two SSE events packed into one bytes chunk."""
        event_a = _anthropic_sse(
            "content_block_delta",
            json.dumps({"delta": {"type": "text_delta", "text": "first"}}),
        )
        event_b = _anthropic_sse(
            "content_block_delta",
            json.dumps({"delta": {"type": "text_delta", "text": "second"}}),
        )
        stop = _anthropic_sse("message_stop", "{}")
        # Combine two events into a single bytes chunk
        combined = event_a + event_b
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter([combined, stop]),
            )
        ]
        assert len(events) == 2
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "first"
        assert isinstance(events[1], StreamDelta)
        assert events[1].text == "second"

    async def test_thinking_and_text_interleaved(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """Thinking delta followed by text delta in the same stream."""
        thinking = _anthropic_sse(
            "content_block_delta",
            json.dumps({"delta": {"type": "thinking_delta", "thinking": "hmm"}}),
        )
        text = _anthropic_sse(
            "content_block_delta",
            json.dumps({"delta": {"type": "text_delta", "text": "answer"}}),
        )
        stop = _anthropic_sse("message_stop", "{}")
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter([thinking, text, stop]),
            )
        ]
        assert len(events) == 2
        assert isinstance(events[0], Thinking)
        assert events[0].text == "hmm"
        assert isinstance(events[1], StreamDelta)
        assert events[1].text == "answer"


class TestOpenAIStreamEdgeCases:
    """Edge cases for OpenAIProvider.parse_stream."""

    async def test_comment_lines_skipped(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """Lines starting with ':' are SSE comments and should be ignored."""
        chunks = [
            b": keepalive\n",
            b'data: {"choices": [{"delta": {"content": "hi"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "hi"

    async def test_empty_stream(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """No chunks at all -- should yield nothing."""
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter([]),
            )
        ]
        assert events == []

    async def test_no_choices_in_data(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """Empty choices array -- no events yielded."""
        chunks = [
            b'data: {"choices": []}\n',
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert events == []

    async def test_chunked_data_across_byte_boundaries(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """A single data line split across two byte chunks."""
        full = b'data: {"choices": [{"delta": {"content": "split"}}]}\n'
        mid = len(full) // 2
        chunk_a = full[:mid]
        chunk_b = full[mid:]
        done = b"data: [DONE]\n\n"
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter([chunk_a, chunk_b, done]),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "split"

    async def test_null_content_in_delta(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """Null content is falsy -- no event yielded."""
        chunks = [
            b'data: {"choices": [{"delta": {"content": null}}]}\n',
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert events == []

    async def test_missing_content_key_in_delta(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """Delta with role but no content key -- delta.get('content') is None."""
        chunks = [
            b'data: {"choices": [{"delta": {"role": "assistant"}}]}\n',
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert events == []

    async def test_non_data_lines_skipped(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """Lines that don't start with 'data: ' are ignored."""
        chunks = [
            b"event: message\n",
            b'data: {"choices": [{"delta": {"content": "ok"}}]}\n',
            b"id: 42\n",
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "ok"


class TestAnthropicStreamToolUse:
    """Tests for Anthropic streaming tool_use."""

    async def test_tool_use_assembled_from_stream(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """Tool use blocks are assembled from content_block_start/delta/stop."""
        chunks = [
            _anthropic_sse(
                "content_block_start",
                json.dumps({
                    "index": 0,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_123",
                        "name": "read_file",
                        "input": {},
                    },
                }),
            ),
            _anthropic_sse(
                "content_block_delta",
                json.dumps({
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path": "/',
                    },
                }),
            ),
            _anthropic_sse(
                "content_block_delta",
                json.dumps({
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": 'etc/hosts"}',
                    },
                }),
            ),
            _anthropic_sse("content_block_stop", "{}"),
            _anthropic_sse("message_stop", "{}"),
        ]
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamToolUse)
        assert events[0].tool_use_id == "toolu_123"
        assert events[0].tool_name == "read_file"
        assert events[0].tool_input == {"path": "/etc/hosts"}

    async def test_text_then_tool_use(
        self,
        anthropic_provider: AnthropicProvider,
    ) -> None:
        """Mixed text and tool_use blocks in one stream."""
        chunks = [
            _anthropic_sse(
                "content_block_delta",
                json.dumps({
                    "delta": {
                        "type": "text_delta",
                        "text": "Let me read that file.",
                    },
                }),
            ),
            _anthropic_sse(
                "content_block_start",
                json.dumps({
                    "index": 1,
                    "content_block": {
                        "type": "tool_use",
                        "id": "toolu_456",
                        "name": "read_file",
                        "input": {},
                    },
                }),
            ),
            _anthropic_sse(
                "content_block_delta",
                json.dumps({
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"path": "/tmp/test"}',
                    },
                }),
            ),
            _anthropic_sse("content_block_stop", "{}"),
            _anthropic_sse("message_stop", "{}"),
        ]
        events = [
            event
            async for event in anthropic_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 2
        assert isinstance(events[0], StreamDelta)
        assert events[0].text == "Let me read that file."
        assert isinstance(events[1], StreamToolUse)
        assert events[1].tool_name == "read_file"


class TestOpenAIStreamToolUse:
    """Tests for OpenAI streaming tool_use."""

    async def test_tool_call_assembled(
        self,
        openai_provider: OpenAIProvider,
    ) -> None:
        """Tool calls are accumulated and yielded on [DONE]."""
        chunks = [
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "id": "call_1", "function": {"name": "read_file", "arguments": ""}}]}}]}\n',  # noqa: E501
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "{\\"path\\": "}}]}}]}\n',  # noqa: E501
            b'data: {"choices": [{"delta": {"tool_calls": [{"index": 0, "function": {"arguments": "\\"/tmp/test\\"}"}}]}}]}\n',  # noqa: E501
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in openai_provider.parse_stream(
                _bytes_iter(chunks),
            )
        ]
        assert len(events) == 1
        assert isinstance(events[0], StreamToolUse)
        assert events[0].tool_use_id == "call_1"
        assert events[0].tool_name == "read_file"
        assert events[0].tool_input == {"path": "/tmp/test"}  # noqa: S108
