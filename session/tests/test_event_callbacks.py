"""Tests for per-event-type callbacks on Session."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from orxtra.session._session import Session
from orxtra.transport import (
    Continuation,
    Result,
    StepFinish,
    StepStart,
    Text,
    ToolUse,
)

from .conftest import MockTraceWriter, MockTransport, make_standard_events

if TYPE_CHECKING:
    import uuid


async def _collect_events(session: Session, message: str) -> list[Any]:
    return [event async for event in session.send(message)]


class TestEventCallbacks:
    async def test_result_callback_fires(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        mock_transport.set_events(make_standard_events(session_id=sid))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        captured: list[Result] = []
        session.on(Result, captured.append)

        await _collect_events(session, "hello")

        assert len(captured) == 1
        assert captured[0].text == "Hello!"

    async def test_step_finish_callback_fires(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"
        mock_transport.set_events(
            make_standard_events(session_id=sid, input_tokens=42, output_tokens=7),
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        captured: list[StepFinish] = []
        session.on(StepFinish, captured.append)

        await _collect_events(session, "test")

        assert len(captured) == 1
        assert captured[0].input_tokens == 42
        assert captured[0].output_tokens == 7

    async def test_multiple_callbacks_same_type(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f"
        mock_transport.set_events(make_standard_events(session_id=sid))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        results_a: list[Result] = []
        results_b: list[Result] = []
        session.on(Result, results_a.append)
        session.on(Result, results_b.append)

        await _collect_events(session, "hello")

        assert len(results_a) == 1
        assert len(results_b) == 1

    async def test_callback_for_unmatched_type_not_called(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80"
        mock_transport.set_events(make_standard_events(session_id=sid))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        captured: list[ToolUse] = []
        session.on(ToolUse, captured.append)

        await _collect_events(session, "hello")

        # No ToolUse events in standard events
        assert len(captured) == 0

    async def test_tool_use_callback(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091"
        events = [
            StepStart(session_id=sid),
            ToolUse(
                tool_name="read_file",
                input={"path": "/test"},
                output="file data",
                status="success",
            ),
            StepFinish(reason="end_turn", input_tokens=5, output_tokens=10),
            Result(text="Done", session_id=sid),
        ]
        mock_transport.set_events(events)
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        captured: list[ToolUse] = []
        session.on(ToolUse, captured.append)

        await _collect_events(session, "read it")

        assert len(captured) == 1
        assert captured[0].tool_name == "read_file"

    async def test_multiple_event_types_registered(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "f6a7b8c9-d0e1-4f2a-3b4c-5d6e7f809102"
        events = [
            StepStart(session_id=sid),
            Text(text="Hello world"),
            StepFinish(reason="end_turn", input_tokens=5, output_tokens=10),
            Result(text="Hello world", session_id=sid),
        ]
        mock_transport.set_events(events)
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        texts: list[Text] = []
        results: list[Result] = []
        session.on(Text, texts.append)
        session.on(Result, results.append)

        await _collect_events(session, "test")

        assert len(texts) == 1
        assert texts[0].text == "Hello world"
        assert len(results) == 1
        assert results[0].text == "Hello world"

    async def test_callbacks_across_multiple_sends(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "a7b8c9d0-e1f2-4a3b-4c5d-6e7f80910213"
        mock_transport.set_events(
            make_standard_events(session_id=sid, text="first"),
            make_standard_events(session_id=sid, text="second"),
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        captured: list[Result] = []
        session.on(Result, captured.append)

        await _collect_events(session, "first")
        await _collect_events(session, "second")

        assert len(captured) == 2
        assert captured[0].text == "first"
        assert captured[1].text == "second"

    async def test_no_callbacks_registered_works(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Session works normally with no callbacks registered."""
        sid = "b8c9d0e1-f2a3-4b4c-5d6e-7f8091021324"
        mock_transport.set_events(make_standard_events(session_id=sid))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )

        events = await _collect_events(session, "hello")
        assert len(events) == 3  # StepStart, StepFinish, Result
