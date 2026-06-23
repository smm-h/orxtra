from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.session._session import Session
from orxtra.session._sync import SyncSession
from orxtra.transport import Result, StepFinish

from .conftest import MockTraceWriter, make_standard_events

if TYPE_CHECKING:
    import uuid

import importlib.util as _ilu
from pathlib import Path

_spec = _ilu.spec_from_file_location(
    "tests.shared_mocks",
    Path(__file__).resolve().parents[2] / "tests" / "shared_mocks.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
MockTransport = _mod.MockTransport


class TestSyncSession:
    def test_send_returns_list_of_events(
        self,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport = MockTransport()
        mock_trace_writer = MockTraceWriter()
        events_to_emit = make_standard_events(
            text="sync response",
        )
        mock_transport.set_events(events_to_emit)

        session = Session(
            transport=mock_transport,  # type: ignore[arg-type]
            model="anthropic/claude-sonnet-4-6",
            system_prompt="test",
            tools=[],
            trace_writer=mock_trace_writer,  # type: ignore[arg-type]
            run_id=run_id,
        )
        sync = SyncSession(session)
        result = sync.send("hello")

        assert isinstance(result, list)
        assert len(result) == len(events_to_emit)
        # Last event should be a Result with our text
        last = result[-1]
        assert isinstance(last, Result)
        assert last.text == "sync response"

    def test_session_id_proxied(
        self,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport = MockTransport()
        mock_trace_writer = MockTraceWriter()
        sid = "e1f2a3b4-c5d6-4e7f-8a9b-0c1d2e3f4a5b"
        mock_transport.set_events(
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
        sync = SyncSession(session)
        assert sync.session_id is None
        sync.send("hello")
        assert sync.session_id == sid

    def test_token_counts_proxied(
        self,
        run_id: uuid.UUID,
    ) -> None:
        mock_transport = MockTransport()
        mock_trace_writer = MockTraceWriter()
        mock_transport.set_events(
            make_standard_events(
                input_tokens=42,
                output_tokens=17,
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
        sync = SyncSession(session)
        sync.send("hello")
        assert sync.total_input_tokens == 42
        assert sync.total_output_tokens == 17
