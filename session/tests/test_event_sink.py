"""Tests for Session EventSink integration."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

import pytest
from orxtra.session._session import Session
from orxtra.transport import Result, StepFinish, StepStart, TransportEvent

from .conftest import MockTraceWriter, MockTransport, make_standard_events

if TYPE_CHECKING:
    import uuid


class MockSink:
    """Records all events dispatched to it."""

    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[TransportEvent] = []
        self._fail = fail

    async def on_event(self, event: TransportEvent) -> None:
        if self._fail:
            msg = "Sink error"
            raise RuntimeError(msg)
        self.events.append(event)


class SlowSink:
    """Sink that takes time to process, for testing close() draining."""

    def __init__(self) -> None:
        self.events: list[TransportEvent] = []
        self.completed = False

    async def on_event(self, event: TransportEvent) -> None:
        await asyncio.sleep(0.01)
        self.events.append(event)
        self.completed = True


async def _collect_events(session: Session, message: str) -> list[TransportEvent]:
    return [event async for event in session.send(message)]


class TestSessionSinks:
    async def test_sink_receives_events(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        mock_transport.set_events(make_standard_events(session_id=sid))
        sink = MockSink()
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[sink],
        )

        await _collect_events(session, "hello")
        # Let tasks complete
        await session.close()

        # Standard events: StepStart, StepFinish, Result
        assert len(sink.events) == 3
        assert isinstance(sink.events[0], StepStart)
        assert isinstance(sink.events[1], StepFinish)
        assert isinstance(sink.events[2], Result)

    async def test_multiple_sinks(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"
        mock_transport.set_events(make_standard_events(session_id=sid))
        sink_a = MockSink()
        sink_b = MockSink()
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[sink_a, sink_b],
        )

        await _collect_events(session, "hello")
        await session.close()

        assert len(sink_a.events) == 3
        assert len(sink_b.events) == 3

    async def test_no_sinks_works(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Session works normally with no sinks."""
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

        events = await _collect_events(session, "hello")
        await session.close()
        assert len(events) == 3

    async def test_failing_sink_does_not_crash(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """A sink that raises should not crash the session."""
        sid = "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80"
        mock_transport.set_events(make_standard_events(session_id=sid))
        failing_sink = MockSink(fail=True)
        good_sink = MockSink()
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[failing_sink, good_sink],
        )

        events = await _collect_events(session, "hello")
        await session.close()

        # Session still yields events normally
        assert len(events) == 3
        # Good sink still receives events
        assert len(good_sink.events) == 3

    async def test_resume_dispatches_to_sinks(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """resume() also dispatches to sinks."""
        from orxtra.transport import Continuation  # noqa: PLC0415

        sid = "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091"
        resume_events = make_standard_events(session_id=sid, text="resumed")
        mock_transport.set_resume_events(resume_events)
        sink = MockSink()
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=sid,
            sinks=[sink],
        )

        cont = Continuation(
            executed_results=[],
            remaining_blocks=[],
            session_id=sid,
        )
        events = [event async for event in session.resume(cont, "result")]
        await session.close()

        assert len(events) == 3
        assert len(sink.events) == 3


class TestSessionClose:
    async def test_close_drains_pending_tasks(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """close() waits for all pending sink tasks to complete."""
        sid = "f6a7b8c9-d0e1-4f2a-3b4c-5d6e7f809102"
        mock_transport.set_events(make_standard_events(session_id=sid))
        slow_sink = SlowSink()
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[slow_sink],
        )

        await _collect_events(session, "hello")
        # Before close, the slow sink may not have finished
        await session.close()
        # After close, all tasks should be drained
        assert slow_sink.completed is True
        assert len(slow_sink.events) > 0

    async def test_close_idempotent(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Calling close() multiple times is safe."""
        sid = "a7b8c9d0-e1f2-4a3b-4c5d-6e7f80910213"
        mock_transport.set_events(make_standard_events(session_id=sid))
        sink = MockSink()
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[sink],
        )

        await _collect_events(session, "hello")
        await session.close()
        await session.close()  # Should not raise

    async def test_close_logs_errors(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """close() logs errors from failed sink tasks."""
        sid = "b8c9d0e1-f2a3-4b4c-5d6e-7f8091021324"
        mock_transport.set_events(make_standard_events(session_id=sid))
        failing_sink = MockSink(fail=True)
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[failing_sink],
        )

        await _collect_events(session, "hello")
        # Should not raise, but should log errors
        await session.close()

    async def test_context_manager_calls_close(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """async with Session calls close() on exit."""
        sid = "c9d0e1f2-a3b4-4c5d-6e7f-809102132435"
        mock_transport.set_events(make_standard_events(session_id=sid))
        sink = MockSink()

        async with Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            sinks=[sink],
        ) as session:
            await _collect_events(session, "hello")

        # After context manager exit, sink tasks should be drained
        assert len(sink.events) == 3
