from __future__ import annotations

from typing import TYPE_CHECKING

from orxt.session._factory import create_session
from orxt.session._session import Session

if TYPE_CHECKING:
    import uuid

    from .conftest import MockTraceWriter, MockTransport


async def _collect(session: Session, msg: str) -> None:
    async for _ in session.send(msg):
        pass


class TestCreateSession:
    async def test_returns_session(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        session = await create_session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        assert isinstance(session, Session)

    async def test_with_session_id(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        session = await create_session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
            session_id="resume-id",
        )
        assert session.session_id == "resume-id"

    async def test_without_session_id(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        session = await create_session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        assert session.session_id is None

    async def test_parameters_stored_correctly(
        self,
        mock_transport: MockTransport,
        mock_trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        session = await create_session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="openai/gpt-4o",
            system_prompt="Be helpful",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        assert session.model == "openai/gpt-4o"
        assert session.system_prompt == "Be helpful"
        assert session.tools == []
