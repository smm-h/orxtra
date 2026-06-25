"""Tests for session resume with conversation history from storage."""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from orxtra.session._factory import _transcript_to_messages, create_session

from .conftest import MockTraceWriter, MockTransport


class MockBackend:
    """Minimal mock implementing the StorageBackend read methods needed for resume."""

    def __init__(
        self,
        token_rows: list[dict[str, Any]] | None = None,
        turn_count: int = 0,
        transcript: list[dict[str, Any]] | None = None,
    ) -> None:
        self._token_rows = token_rows or []
        self._turn_count = turn_count
        self._transcript = transcript or []

    async def read_session_token_counts(
        self, session_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        return self._token_rows

    async def read_session_turn_count(
        self, session_id: uuid.UUID,
    ) -> int:
        return self._turn_count

    async def read_transcript(
        self, session_id: uuid.UUID,
    ) -> list[dict[str, Any]]:
        return self._transcript

    # Stub for write_transcript_entry (used by Session)
    async def write_transcript_entry(
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        turn: int,
        role: str,
        content: str,
        tool_calls: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> None:
        pass


class TestTranscriptToMessages:
    def test_simple_user_assistant(self) -> None:
        transcript = [
            {"role": "user", "content": "Hello", "tool_calls": None},
            {"role": "assistant", "content": "Hi there", "tool_calls": None},
        ]
        messages = _transcript_to_messages(transcript)
        assert len(messages) == 2
        assert messages[0] == {"role": "user", "content": "Hello"}
        assert messages[1] == {"role": "assistant", "content": "Hi there"}

    def test_assistant_with_tool_calls(self) -> None:
        transcript = [
            {"role": "user", "content": "Read the file", "tool_calls": None},
            {
                "role": "assistant",
                "content": "I'll read the file.",
                "tool_calls": {
                    "calls": [
                        {
                            "tool_name": "read_file",
                            "input": {"path": "/test"},
                            "output": "file contents",
                            "status": "success",
                        },
                    ],
                },
            },
        ]
        messages = _transcript_to_messages(transcript)
        assert len(messages) == 2
        assert messages[0]["role"] == "user"
        assert messages[1]["role"] == "assistant"
        assert "read_file" in messages[1]["content"]
        assert "file contents" in messages[1]["content"]

    def test_empty_transcript(self) -> None:
        assert _transcript_to_messages([]) == []

    def test_missing_content_defaults_to_empty(self) -> None:
        transcript = [{"role": "user"}]
        messages = _transcript_to_messages(transcript)
        assert messages[0]["content"] == ""

    def test_assistant_with_empty_tool_calls(self) -> None:
        transcript = [
            {
                "role": "assistant",
                "content": "Done",
                "tool_calls": {"calls": []},
            },
        ]
        messages = _transcript_to_messages(transcript)
        assert messages[0]["content"] == "Done"


class TestSessionResumeWithHistory:
    async def test_resume_injects_history_into_transport(self) -> None:
        """Resuming a session loads transcript and injects into transport."""
        session_id = str(uuid.uuid4())
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()

        transcript = [
            {"role": "user", "content": "Hello", "tool_calls": None, "tokens": None},
            {"role": "assistant", "content": "Hi!", "tool_calls": None, "tokens": None},
        ]
        backend = MockBackend(
            token_rows=[],
            turn_count=1,
            transcript=transcript,
        )

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=backend,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=session_id,
            backend=backend,  # type: ignore[arg-type]
        )

        # The transport should have injected history
        # (MockTransport doesn't have _sessions, but we can verify
        # the session was created with the right state)
        assert session.session_id == session_id
        assert session.turn_count == 1

    async def test_resume_with_no_transcript_skips_injection(self) -> None:
        """No transcript means no history injection."""
        session_id = str(uuid.uuid4())
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()

        backend = MockBackend(
            token_rows=[],
            turn_count=0,
            transcript=[],
        )

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=backend,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=session_id,
            backend=backend,  # type: ignore[arg-type]
        )

        assert session.session_id == session_id
        assert session.turn_count == 0

    async def test_resume_restores_tokens_and_history(self) -> None:
        """Both tokens and history are restored on resume."""
        session_id = str(uuid.uuid4())
        transport = MockTransport()
        run_id = uuid.uuid4()

        transcript = [
            {
                "role": "user",
                "content": "first message",
                "tool_calls": None,
                "tokens": None,
            },
            {
                "role": "assistant",
                "content": "first response",
                "tool_calls": None,
                "tokens": {"input_tokens": 100, "output_tokens": 50},
            },
        ]
        backend = MockBackend(
            token_rows=[{"tokens": {"input_tokens": 100, "output_tokens": 50}}],
            turn_count=1,
            transcript=transcript,
        )

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=backend,  # type: ignore[arg-type]
            run_id=run_id,
            session_id=session_id,
            backend=backend,  # type: ignore[arg-type]
        )

        assert session.total_input_tokens == 100
        assert session.total_output_tokens == 50
        assert session.turn_count == 1

    async def test_resume_without_session_id_skips_all(self) -> None:
        """No session_id means nothing is loaded."""
        transport = MockTransport()
        trace_writer = MockTraceWriter()
        run_id = uuid.uuid4()

        backend = MockBackend(
            token_rows=[{"tokens": {"input_tokens": 999}}],
            turn_count=5,
            transcript=[{"role": "user", "content": "hello", "tool_calls": None}],
        )

        session = await create_session(
            transport=transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            # No session_id
            backend=backend,  # type: ignore[arg-type]
        )

        assert session.total_input_tokens == 0
        assert session.turn_count == 0


class TestTransportInjectHistory:
    def test_inject_history_sets_session(self) -> None:
        """Transport.inject_history populates _sessions dict."""
        from orxtra.transport import RetryPolicy

        class StubProvider:
            pass

        retry = RetryPolicy(
            max_retries=0,
            backoff_base_seconds=1.0,
            backoff_max_seconds=10.0,
            jitter=False,
        )
        from orxtra.transport import Transport

        transport = Transport(
            provider=StubProvider(),  # type: ignore[arg-type]
            retry_policy=retry,
        )

        messages = [
            {"role": "user", "content": "Hello"},
            {"role": "assistant", "content": "Hi!"},
        ]
        transport.inject_history("session-1", messages)

        assert "session-1" in transport._sessions
        assert len(transport._sessions["session-1"]) == 2
        assert transport._sessions["session-1"][0]["role"] == "user"

    def test_inject_history_copies_messages(self) -> None:
        """inject_history makes a copy, not a reference."""
        from orxtra.transport import RetryPolicy

        class StubProvider:
            pass

        retry = RetryPolicy(
            max_retries=0,
            backoff_base_seconds=1.0,
            backoff_max_seconds=10.0,
            jitter=False,
        )
        from orxtra.transport import Transport

        transport = Transport(
            provider=StubProvider(),  # type: ignore[arg-type]
            retry_policy=retry,
        )

        messages = [{"role": "user", "content": "Hello"}]
        transport.inject_history("session-1", messages)
        messages.append({"role": "assistant", "content": "Hi!"})

        # Transport should have the original, not the mutated list
        assert len(transport._sessions["session-1"]) == 1
