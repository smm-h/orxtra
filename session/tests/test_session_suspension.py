from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.session._session import Session
from orxtra.transport import (
    Continuation,
    Result,
    SessionSuspended,
    StepFinish,
    StepStart,
    ToolUse,
)

if TYPE_CHECKING:
    import uuid

    from .conftest import MockTraceWriter, MockTransport


async def _collect_events(session: Session, message: str) -> list[Any]:
    return [event async for event in session.send(message)]


async def _collect_resume_events(
    session: Session,
    continuation: Continuation,
    result: str,
) -> list[Any]:
    return [event async for event in session.resume(continuation, result)]


class TestSessionSendYieldsSuspended:
    """Session.send() yields SessionSuspended when transport suspends."""

    async def test_send_yields_session_suspended(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "a1b2c3d4-e5f6-4a7b-8c9d-0e1f2a3b4c5d"
        continuation = Continuation(
            executed_results=[
                {"type": "tool_result", "tool_use_id": "tu_1", "content": "waiting"}
            ],
            remaining_blocks=[],
            session_id=sid,
            messages=[],
        )
        events_from_transport = [
            StepStart(session_id=sid),
            ToolUse(
                tool_name="await_tool",
                input={"x": "request"},
                output="waiting",
                status="success",
            ),
            SessionSuspended(
                continuation=continuation,
                session_id=sid,
            ),
        ]
        mock_transport.set_events(events_from_transport)
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        received = await _collect_events(session, "start task")

        # SessionSuspended should be yielded
        suspended = [e for e in received if isinstance(e, SessionSuspended)]
        assert len(suspended) == 1

        # No Result event
        results = [e for e in received if isinstance(e, Result)]
        assert len(results) == 0

        # Session ID should be captured from SessionSuspended
        assert session.session_id == sid


class TestSessionResumeContinuesNormally:
    """Session.resume() continues normally after suspension."""

    async def test_resume_yields_result(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "b2c3d4e5-f6a7-4b8c-9d0e-1f2a3b4c5d6e"
        continuation = Continuation(
            executed_results=[],
            remaining_blocks=[],
            session_id=sid,
            messages=[],
        )

        # First: send triggers suspension
        send_events = [
            StepStart(session_id=sid),
            ToolUse(
                tool_name="await_tool",
                input={"x": "1"},
                output="waiting",
                status="success",
            ),
            SessionSuspended(continuation=continuation, session_id=sid),
        ]
        mock_transport.set_events(send_events)

        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "go")

        # Resume events
        resume_events_from_transport = [
            StepFinish(
                reason="end_turn",
                input_tokens=50,
                output_tokens=25,
            ),
            Result(
                text="Completed after resume",
                session_id=sid,
                total_input_tokens=50,
                total_output_tokens=25,
            ),
        ]
        mock_transport.set_resume_events(resume_events_from_transport)

        resume_received = await _collect_resume_events(
            session, continuation, "approved"
        )

        results = [e for e in resume_received if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Completed after resume"


class TestTokensAccumulateAcrossSuspension:
    """Token counts accumulate across suspension boundary."""

    async def test_tokens_accumulate(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "c3d4e5f6-a7b8-4c9d-0e1f-2a3b4c5d6e7f"
        continuation = Continuation(
            executed_results=[],
            remaining_blocks=[],
            session_id=sid,
            messages=[],
        )

        # Send: no StepFinish (suspended before completion)
        send_events = [
            StepStart(session_id=sid),
            SessionSuspended(continuation=continuation, session_id=sid),
        ]
        mock_transport.set_events(send_events)

        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "go")
        assert session.total_input_tokens == 0  # No StepFinish before suspension

        # Resume with tokens
        resume_events = [
            StepFinish(
                reason="end_turn",
                input_tokens=100,
                output_tokens=50,
                reasoning_tokens=10,
                cache_read_tokens=20,
                cache_write_tokens=5,
            ),
            Result(
                text="Done",
                session_id=sid,
                total_input_tokens=100,
                total_output_tokens=50,
            ),
        ]
        mock_transport.set_resume_events(resume_events)

        await _collect_resume_events(session, continuation, "result")

        assert session.total_input_tokens == 100
        assert session.total_output_tokens == 50
        assert session.total_reasoning_tokens == 10
        assert session.total_cache_read_tokens == 20
        assert session.total_cache_write_tokens == 5


class TestTranscriptForSuspendedSession:
    """Transcript entries written for both pre-suspend send and post-resume."""

    async def test_transcript_entries_on_suspend_and_resume(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80"
        continuation = Continuation(
            executed_results=[],
            remaining_blocks=[],
            session_id=sid,
            messages=[],
        )

        # Send with tool use before suspension
        send_events = [
            StepStart(session_id=sid),
            ToolUse(
                tool_name="await_tool",
                input={"x": "1"},
                output="waiting",
                status="success",
            ),
            SessionSuspended(continuation=continuation, session_id=sid),
        ]
        mock_transport.set_events(send_events)

        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "start task")

        # Pre-suspend transcript: user + assistant entries
        assert len(mock_trace_writer.transcript_entries) == 2
        user_entry = mock_trace_writer.transcript_entries[0]
        assert user_entry["role"] == "user"
        assert user_entry["content"] == "start task"
        assert user_entry["turn"] == 1

        assistant_entry = mock_trace_writer.transcript_entries[1]
        assert assistant_entry["role"] == "assistant"
        assert assistant_entry["content"] == ""  # No result text when suspended
        assert assistant_entry["tool_calls"]["calls"][0]["tool_name"] == "await_tool"
        assert assistant_entry["turn"] == 1

        # Resume
        resume_events = [
            StepFinish(reason="end_turn", input_tokens=30, output_tokens=15),
            Result(text="All done", session_id=sid),
        ]
        mock_transport.set_resume_events(resume_events)

        await _collect_resume_events(session, continuation, "approved")

        # Post-resume transcript: user + assistant (turn 2)
        assert len(mock_trace_writer.transcript_entries) == 4
        resume_user = mock_trace_writer.transcript_entries[2]
        assert resume_user["role"] == "user"
        assert "[resume: approved]" in resume_user["content"]
        assert resume_user["turn"] == 2

        resume_assistant = mock_trace_writer.transcript_entries[3]
        assert resume_assistant["role"] == "assistant"
        assert resume_assistant["content"] == "All done"
        assert resume_assistant["turn"] == 2
        assert resume_assistant["tokens"]["input_tokens"] == 30
        assert resume_assistant["tokens"]["output_tokens"] == 15
