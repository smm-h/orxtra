from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest
from orxtra.transport._events import UnknownEvent
from orxtra.transport.providers._anthropic import AnthropicProvider
from orxtra.transport.providers._openai import OpenAIProvider

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _bytes_iter(chunks: list[bytes]) -> AsyncIterator[bytes]:
    for chunk in chunks:
        yield chunk


def _sse_chunk(event: str, data: str) -> bytes:
    return f"event: {event}\ndata: {data}\n\n".encode()


# ---------------------------------------------------------------------------
# Anthropic -- parse_response
# ---------------------------------------------------------------------------


class TestAnthropicParseResponseUnknown:
    @pytest.fixture
    def provider(self) -> AnthropicProvider:
        return AnthropicProvider(api_key="test-key")

    def test_unrecognized_block_type_preserved(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response: dict[str, Any] = {
            "content": [
                {"type": "text", "text": "Hello"},
                {"type": "server_tool_use", "id": "st_1", "name": "web"},
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 2
        assert blocks[0].type == "text"
        assert blocks[1].type == "server_tool_use"
        # Raw JSON is stored in the text field
        raw = json.loads(blocks[1].text or "{}")
        assert raw["type"] == "server_tool_use"
        assert raw["id"] == "st_1"

    def test_multiple_unknown_types(
        self,
        provider: AnthropicProvider,
    ) -> None:
        response: dict[str, Any] = {
            "content": [
                {"type": "citation", "source": "paper.pdf"},
                {"type": "image", "url": "https://example.com/img.png"},
            ],
        }
        blocks = provider.parse_response(response)
        assert len(blocks) == 2
        assert blocks[0].type == "citation"
        assert blocks[1].type == "image"


# ---------------------------------------------------------------------------
# Anthropic -- parse_stream unknown delta types
# ---------------------------------------------------------------------------


class TestAnthropicParseStreamUnknownDelta:
    @pytest.fixture
    def provider(self) -> AnthropicProvider:
        return AnthropicProvider(api_key="test-key")

    async def test_unknown_delta_type_yields_unknown_event(
        self,
        provider: AnthropicProvider,
    ) -> None:
        chunks = [
            _sse_chunk(
                "content_block_delta",
                json.dumps({
                    "delta": {"type": "citation_delta", "source": "ref.txt"},
                }),
            ),
            _sse_chunk("message_stop", "{}"),
        ]
        events = [
            event async for event in provider.parse_stream(_bytes_iter(chunks))
        ]
        assert len(events) == 1
        assert isinstance(events[0], UnknownEvent)
        assert events[0].raw["type"] == "citation_delta"
        assert events[0].raw["source"] == "ref.txt"


# ---------------------------------------------------------------------------
# Anthropic -- parse_stream unknown SSE event types
# ---------------------------------------------------------------------------


class TestAnthropicParseStreamUnknownEventType:
    @pytest.fixture
    def provider(self) -> AnthropicProvider:
        return AnthropicProvider(api_key="test-key")

    async def test_unknown_sse_event_yields_unknown_event(
        self,
        provider: AnthropicProvider,
    ) -> None:
        chunks = [
            _sse_chunk("server_tool_start", json.dumps({"id": "st_1"})),
            _sse_chunk("message_stop", "{}"),
        ]
        events = [
            event async for event in provider.parse_stream(_bytes_iter(chunks))
        ]
        assert len(events) == 1
        assert isinstance(events[0], UnknownEvent)
        assert events[0].raw["id"] == "st_1"

    async def test_known_ignored_events_not_emitted(
        self,
        provider: AnthropicProvider,
    ) -> None:
        """message_start and ping are expected silent events, not unknown."""
        chunks = [
            _sse_chunk("message_start", json.dumps({"type": "message"})),
            _sse_chunk("ping", json.dumps({})),
            _sse_chunk("message_stop", "{}"),
        ]
        events = [
            event async for event in provider.parse_stream(_bytes_iter(chunks))
        ]
        # Neither message_start nor ping should produce UnknownEvent
        unknown = [e for e in events if isinstance(e, UnknownEvent)]
        assert len(unknown) == 0


# ---------------------------------------------------------------------------
# OpenAI -- parse_stream unknown delta keys
# ---------------------------------------------------------------------------


class TestOpenAIParseStreamUnknown:
    @pytest.fixture
    def provider(self) -> OpenAIProvider:
        return OpenAIProvider(api_key="test-key")

    async def test_unknown_delta_key_yields_unknown_event(
        self,
        provider: OpenAIProvider,
    ) -> None:
        chunks = [
            json.dumps({
                "choices": [{
                    "delta": {
                        "content": "Hello",
                        "audio": {"data": "base64..."},
                    },
                }],
            }).encode(),
        ]
        # Wrap in SSE format
        sse_chunks = [
            b"data: " + chunks[0] + b"\n\n",
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in provider.parse_stream(_bytes_iter(sse_chunks))
        ]
        from orxtra.transport._events import StreamDelta

        stream_deltas = [e for e in events if isinstance(e, StreamDelta)]
        unknown_events = [e for e in events if isinstance(e, UnknownEvent)]
        assert len(stream_deltas) == 1
        assert stream_deltas[0].text == "Hello"
        assert len(unknown_events) == 1
        assert "audio" in unknown_events[0].raw

    async def test_no_unknown_for_standard_delta(
        self,
        provider: OpenAIProvider,
    ) -> None:
        sse_chunks = [
            b'data: {"choices": [{"delta": {"role": "assistant", "content": "Hi"}}]}\n\n',
            b"data: [DONE]\n\n",
        ]
        events = [
            event
            async for event in provider.parse_stream(_bytes_iter(sse_chunks))
        ]
        unknown_events = [e for e in events if isinstance(e, UnknownEvent)]
        assert len(unknown_events) == 0
