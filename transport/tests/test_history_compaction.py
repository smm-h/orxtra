from __future__ import annotations

from typing import Any

import httpx
import respx
from orxtra.transport._events import (
    ContentBlock,
    Event,
    Result,
    Usage,
)
from orxtra.transport._provider import RetryPolicy
from orxtra.transport._transport import Transport

from .test_transport import CapturingProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_URL = "https://mock.api/v1/messages"
_OK_RESPONSE = httpx.Response(200, json={"mock": True})


def _retry_policy() -> RetryPolicy:
    return RetryPolicy(
        max_retries=3,
        backoff_base_seconds=0.001,
        backoff_max_seconds=0.01,
        jitter=False,
    )


async def _collect(
    transport: Transport, message: str, **kwargs: Any,  # noqa: ANN401
) -> list[Event]:
    return [event async for event in transport.send(message, **kwargs)]


def _default_send_kwargs(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "model": "test-model",
        "system_prompt": "sys",
        "tools": [],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestHistoryCompaction:
    @respx.mock
    async def test_no_compaction_when_none(self) -> None:
        """When max_history_turns is None, all history is kept."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = CapturingProvider(
            responses=[
                ([ContentBlock(type="text", text="Reply 1")], Usage()),
                ([ContentBlock(type="text", text="Reply 2")], Usage()),
                ([ContentBlock(type="text", text="Reply 3")], Usage()),
            ],
        )
        transport = Transport(
            provider=provider,
            retry_policy=_retry_policy(),
            max_history_turns=None,
        )
        sid = "sess-1"

        await _collect(transport, "Msg 1", **_default_send_kwargs(session_id=sid))
        await _collect(transport, "Msg 2", **_default_send_kwargs(session_id=sid))
        await _collect(transport, "Msg 3", **_default_send_kwargs(session_id=sid))

        # Third call sees all 5 messages (3 user + 2 assistant)
        assert len(provider.captured_messages[2]) == 5

    @respx.mock
    async def test_compaction_keeps_last_n_turns(self) -> None:
        """When max_history_turns=2, only the last 2 turns are kept."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = CapturingProvider(
            responses=[
                ([ContentBlock(type="text", text="Reply 1")], Usage()),
                ([ContentBlock(type="text", text="Reply 2")], Usage()),
                ([ContentBlock(type="text", text="Reply 3")], Usage()),
            ],
        )
        transport = Transport(
            provider=provider,
            retry_policy=_retry_policy(),
            max_history_turns=2,
        )
        sid = "sess-2"

        await _collect(transport, "Msg 1", **_default_send_kwargs(session_id=sid))
        await _collect(transport, "Msg 2", **_default_send_kwargs(session_id=sid))
        await _collect(transport, "Msg 3", **_default_send_kwargs(session_id=sid))

        # Third call: after compaction to 2 turns, the API sees:
        # user "Msg 2", assistant "Reply 2", user "Msg 3"
        # That's 3 messages (turn 2 = user+assistant, turn 3 = user)
        msgs = provider.captured_messages[2]
        assert len(msgs) == 3
        assert msgs[0]["content"] == "Msg 2"
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["content"] == "Msg 3"
        assert msgs[2]["role"] == "user"

    @respx.mock
    async def test_compaction_with_one_turn(self) -> None:
        """max_history_turns=1 keeps only the current turn."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = CapturingProvider(
            responses=[
                ([ContentBlock(type="text", text="Reply 1")], Usage()),
                ([ContentBlock(type="text", text="Reply 2")], Usage()),
            ],
        )
        transport = Transport(
            provider=provider,
            retry_policy=_retry_policy(),
            max_history_turns=1,
        )
        sid = "sess-3"

        await _collect(transport, "Msg 1", **_default_send_kwargs(session_id=sid))
        await _collect(transport, "Msg 2", **_default_send_kwargs(session_id=sid))

        # Second call: only the latest user message
        msgs = provider.captured_messages[1]
        assert len(msgs) == 1
        assert msgs[0]["content"] == "Msg 2"
        assert msgs[0]["role"] == "user"

    @respx.mock
    async def test_compaction_under_limit(self) -> None:
        """No compaction when history is under the limit."""
        respx.post(_MOCK_URL).mock(return_value=_OK_RESPONSE)

        provider = CapturingProvider(
            responses=[
                ([ContentBlock(type="text", text="Reply 1")], Usage()),
                ([ContentBlock(type="text", text="Reply 2")], Usage()),
            ],
        )
        transport = Transport(
            provider=provider,
            retry_policy=_retry_policy(),
            max_history_turns=10,
        )
        sid = "sess-4"

        await _collect(transport, "Msg 1", **_default_send_kwargs(session_id=sid))
        await _collect(transport, "Msg 2", **_default_send_kwargs(session_id=sid))

        # Second call: all 3 messages present (under limit)
        msgs = provider.captured_messages[1]
        assert len(msgs) == 3
        assert msgs[0]["content"] == "Msg 1"
        assert msgs[1]["role"] == "assistant"
        assert msgs[2]["content"] == "Msg 2"


class TestCompactHistoryDirect:
    """Unit tests for the _compact_history static method."""

    def test_empty_history(self) -> None:
        history: list[dict[str, Any]] = []
        Transport._compact_history(history, 2)
        assert history == []

    def test_zero_max_turns_no_op(self) -> None:
        history = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
        ]
        Transport._compact_history(history, 0)
        # Zero means no-op (can't keep zero turns meaningfully)
        assert len(history) == 2

    def test_exact_limit(self) -> None:
        history = [
            {"role": "user", "content": "A"},
            {"role": "assistant", "content": "B"},
            {"role": "user", "content": "C"},
        ]
        Transport._compact_history(history, 2)
        # Exactly 2 turns, no compaction needed
        assert len(history) == 3

    def test_tool_messages_preserved(self) -> None:
        """Tool result messages belong to the same turn as their user message."""
        history = [
            {"role": "user", "content": "First"},
            {"role": "assistant", "content": "Reply 1"},
            {"role": "user", "content": [{"type": "tool_result"}]},
            {"role": "assistant", "content": "Reply after tool"},
            {"role": "user", "content": "Last"},
        ]
        Transport._compact_history(history, 2)
        # Keeps from second user message onward: user(tool_result), assistant, user(Last)
        assert len(history) == 3
        assert history[0]["content"] == [{"type": "tool_result"}]
        assert history[1]["role"] == "assistant"
        assert history[2]["content"] == "Last"
