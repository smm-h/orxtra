from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from orxt.session._session import Session
from orxt.transport import Error, Result, StepFinish, StepStart, Text, ToolUse

from .conftest import MockTraceWriter, MockTransport, make_standard_events

if TYPE_CHECKING:
    import uuid


async def _collect_events(session: Session, message: str) -> list[Any]:
    return [event async for event in session.send(message)]


class TestSessionId:
    async def test_first_send_populates_session_id(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "b263c568-f440-4c15-b03f-ae7b43d91f92"
        mock_transport.set_events(make_standard_events(session_id=sid))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        assert session.session_id is None
        await _collect_events(session, "hello")
        assert session.session_id == sid

    async def test_resume_id_before_send_raises(self, session: Session) -> None:
        with pytest.raises(RuntimeError, match="No session ID"):
            session.resume_id()

    async def test_resume_id_after_send(
        self,
        session: Session,
        mock_transport: MockTransport,
    ) -> None:
        sid = "fc279ac7-10c3-4125-9712-e09b55c77899"
        mock_transport.set_events(make_standard_events(session_id=sid))
        await _collect_events(session, "hi")
        assert session.resume_id() == sid

    async def test_session_with_preset_session_id(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "91ff00da-437c-4551-be95-d32c5a556240"
        mock_transport.set_events(make_standard_events(session_id=sid))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=sid,
        )
        assert session.session_id == sid
        await _collect_events(session, "hello")
        assert mock_transport.calls[0]["session_id"] == sid

    async def test_multiple_sends_maintain_session_id(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "d4e5f6a7-b8c9-4d0e-1f2a-3b4c5d6e7f80"
        mock_transport.set_events(
            make_standard_events(session_id=sid),
            make_standard_events(session_id=sid),
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "first")
        await _collect_events(session, "second")
        assert mock_transport.calls[1]["session_id"] == sid


class TestTokenAccumulation:
    async def test_tokens_accumulated_across_sends(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport.set_events(
            make_standard_events(input_tokens=100, output_tokens=50),
            make_standard_events(input_tokens=200, output_tokens=75),
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "first")
        await _collect_events(session, "second")
        assert session.total_input_tokens == 300
        assert session.total_output_tokens == 125

    async def test_all_five_token_types_accumulated(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport.set_events(make_standard_events(
            input_tokens=10,
            output_tokens=20,
            reasoning_tokens=5,
            cache_read_tokens=30,
            cache_write_tokens=15,
        ))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "test")
        assert session.total_input_tokens == 10
        assert session.total_output_tokens == 20
        assert session.total_reasoning_tokens == 5
        assert session.total_cache_read_tokens == 30
        assert session.total_cache_write_tokens == 15

    async def test_zero_tokens_in_step_finish(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport.set_events(make_standard_events(
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ))
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "test")
        assert session.total_input_tokens == 0
        assert session.total_output_tokens == 0

    async def test_token_accumulation_with_tool_call_loop(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Transport may yield multiple StepFinish events in a tool-call loop."""
        sid = "e5f6a7b8-c9d0-4e1f-2a3b-4c5d6e7f8091"
        events = [
            StepStart(session_id=sid),
            StepFinish(reason="tool_use", input_tokens=50, output_tokens=30),
            ToolUse(
                tool_name="read_file",
                input={"path": "/tmp/test"},  # noqa: S108
                output="file contents",
                status="success",
            ),
            StepFinish(reason="end_turn", input_tokens=80, output_tokens=40),
            Result(
                text="Done",
                session_id=sid,
                total_input_tokens=130,
                total_output_tokens=70,
            ),
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
        await _collect_events(session, "use tool")
        assert session.total_input_tokens == 130
        assert session.total_output_tokens == 70


class TestTurnCount:
    async def test_turn_count_increments(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport.set_events(
            make_standard_events(),
            make_standard_events(),
            make_standard_events(),
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "one")
        await _collect_events(session, "two")
        await _collect_events(session, "three")
        assert session.turn_count == 3


class TestEventPassthrough:
    async def test_all_events_pass_through(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "f6a7b8c9-d0e1-4f2a-3b4c-5d6e7f809102"
        source_events = [
            StepStart(session_id=sid),
            Text(text="Hello world"),
            StepFinish(reason="end_turn", input_tokens=5, output_tokens=10),
            Result(text="Hello world", session_id=sid),
        ]
        mock_transport.set_events(source_events)
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        received = await _collect_events(session, "test")
        assert received == source_events

    async def test_tool_call_events_pass_through(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        tool_use = ToolUse(
            tool_name="read_file",
            input={"path": "/test"},
            output="contents",
            status="success",
        )
        sid = "a7b8c9d0-e1f2-4a3b-4c5d-6e7f80910213"
        source_events = [
            StepStart(session_id=sid),
            tool_use,
            StepFinish(reason="end_turn"),
            Result(text="Done", session_id=sid),
        ]
        mock_transport.set_events(source_events)
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        received = await _collect_events(session, "test")
        assert tool_use in received


class TestTranscriptPersistence:
    async def test_user_message_persisted(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
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
        await _collect_events(session, "my user message")
        user_entries = [
            e for e in mock_trace_writer.transcript_entries if e["role"] == "user"
        ]
        assert len(user_entries) == 1
        assert user_entries[0]["content"] == "my user message"
        assert user_entries[0]["run_id"] == run_id
        assert user_entries[0]["turn"] == 1

    async def test_assistant_response_persisted(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "c9d0e1f2-a3b4-4c5d-6e7f-809102132435"
        mock_transport.set_events(
            make_standard_events(session_id=sid, text="I am the assistant")
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "hi")
        assistant_entries = [
            e for e in mock_trace_writer.transcript_entries if e["role"] == "assistant"
        ]
        assert len(assistant_entries) == 1
        assert assistant_entries[0]["content"] == "I am the assistant"
        assert assistant_entries[0]["turn"] == 1

    async def test_tool_calls_in_transcript(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "d0e1f2a3-b4c5-4d6e-7f80-910213243546"
        events = [
            StepStart(session_id=sid),
            ToolUse(
                tool_name="read_file",
                input={"path": "/test"},
                output="file data",
                status="success",
            ),
            StepFinish(reason="end_turn"),
            Result(text="Read it", session_id=sid),
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
        await _collect_events(session, "read the file")
        assistant_entries = [
            e for e in mock_trace_writer.transcript_entries if e["role"] == "assistant"
        ]
        assert len(assistant_entries) == 1
        assert assistant_entries[0]["tool_calls"] is not None
        calls = assistant_entries[0]["tool_calls"]["calls"]
        assert len(calls) == 1
        assert calls[0]["tool_name"] == "read_file"
        assert calls[0]["output"] == "file data"

    async def test_multi_turn_transcript_has_per_turn_tokens(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "e1f2a3b4-c5d6-4e7f-8091-021324354657"
        mock_transport.set_events(
            make_standard_events(
                session_id=sid, input_tokens=100, output_tokens=50,
                reasoning_tokens=10, cache_read_tokens=20, cache_write_tokens=5,
            ),
            make_standard_events(
                session_id=sid, input_tokens=200, output_tokens=75,
                reasoning_tokens=15, cache_read_tokens=30, cache_write_tokens=8,
            ),
        )
        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        await _collect_events(session, "first")
        await _collect_events(session, "second")

        assistant_entries = [
            e for e in mock_trace_writer.transcript_entries if e["role"] == "assistant"
        ]
        assert len(assistant_entries) == 2

        # First turn: tokens should be exactly the first turn's values
        t1 = assistant_entries[0]["tokens"]
        assert t1["input_tokens"] == 100
        assert t1["output_tokens"] == 50
        assert t1["reasoning_tokens"] == 10
        assert t1["cache_read_tokens"] == 20
        assert t1["cache_write_tokens"] == 5

        # Second turn: tokens should be only the second turn's values, NOT cumulative
        t2 = assistant_entries[1]["tokens"]
        assert t2["input_tokens"] == 200
        assert t2["output_tokens"] == 75
        assert t2["reasoning_tokens"] == 15
        assert t2["cache_read_tokens"] == 30
        assert t2["cache_write_tokens"] == 8


class TestErrorHandling:
    async def test_error_event_passes_through(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        sid = "f2a3b4c5-d6e7-4f80-9102-132435465768"
        error_event = Error(name="overloaded_error", message="Server overloaded")
        events = [
            StepStart(session_id=sid),
            error_event,
            StepFinish(reason="end_turn", input_tokens=5, output_tokens=10),
            Result(text="Recovered", session_id=sid),
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
        received = await _collect_events(session, "test")
        assert error_event in received

    async def test_no_result_event_leaves_session_id_none(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        events = [
            StepStart(session_id="ignored-in-step-start"),
            StepFinish(reason="end_turn", input_tokens=5, output_tokens=10),
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
        received = await _collect_events(session, "test")
        assert len(received) == 2
        assert session.session_id is None
        # No transcript entries should be written since no Result was received
        assert len(mock_trace_writer.transcript_entries) == 0
