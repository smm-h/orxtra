from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock

import pytest
from orxt.session._session import Session
from orxt.transport import Continuation, Event, Result, StepFinish, StepStart

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orxt.protocols import Tool


class MockTransport:
    """Transport mock that yields configurable event sequences."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self._event_sequences: list[list[Event]] = []
        self.resume_calls: list[dict[str, Any]] = []
        self._resume_event_sequences: list[list[Event]] = []

    def set_events(self, *sequences: list[Event]) -> None:
        self._event_sequences = list(sequences)

    def set_resume_events(self, *sequences: list[Event]) -> None:
        self._resume_event_sequences = list(sequences)

    async def send(  # noqa: PLR0913
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        self.calls.append({
            "message": message,
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
            "session_id": session_id,
            "stream_deltas": stream_deltas,
        })
        events = self._event_sequences.pop(0) if self._event_sequences else []
        for event in events:
            yield event

    async def resume(  # noqa: PLR0913
        self,
        continuation: Continuation,
        await_result: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        self.resume_calls.append({
            "continuation": continuation,
            "await_result": await_result,
            "model": model,
            "system_prompt": system_prompt,
            "tools": tools,
            "stream_deltas": stream_deltas,
        })
        events = (
            self._resume_event_sequences.pop(0)
            if self._resume_event_sequences
            else []
        )
        for event in events:
            yield event


class MockTraceWriter:
    """Records all write_transcript_entry calls."""

    def __init__(self) -> None:
        self.transcript_entries: list[dict[str, Any]] = []
        self.write_transcript_entry = AsyncMock(side_effect=self._record_entry)

    async def _record_entry(  # noqa: PLR0913
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        turn: int,
        role: str,
        content: str,
        tool_calls: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> None:
        self.transcript_entries.append({
            "session_id": session_id,
            "run_id": run_id,
            "turn": turn,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tokens": tokens,
        })


def make_standard_events(  # noqa: PLR0913
    session_id: str = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d",
    text: str = "Hello!",
    input_tokens: int = 10,
    output_tokens: int = 20,
    reasoning_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> list[Event]:
    return [
        StepStart(session_id=session_id),
        StepFinish(
            reason="end_turn",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            reasoning_tokens=reasoning_tokens,
            cache_read_tokens=cache_read_tokens,
            cache_write_tokens=cache_write_tokens,
        ),
        Result(
            text=text,
            session_id=session_id,
            total_input_tokens=input_tokens,
            total_output_tokens=output_tokens,
            total_reasoning_tokens=reasoning_tokens,
            total_cache_read_tokens=cache_read_tokens,
            total_cache_write_tokens=cache_write_tokens,
        ),
    ]


@pytest.fixture
def mock_transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def mock_trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid.uuid4()


@pytest.fixture
def session(
    mock_transport: MockTransport,
    mock_trace_writer: MockTraceWriter,
    run_id: uuid.UUID,
) -> Session:
    return Session(
        transport=mock_transport,  # type: ignore[arg-type]
        model="anthropic/claude-sonnet-4-6",
        system_prompt="You are helpful.",
        tools=[],
        trace_writer=mock_trace_writer,  # type: ignore[arg-type]
        run_id=run_id,
    )
