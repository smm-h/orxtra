"""Tests for RunManager -- active run registry and sink subscription."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

from orxtra.services._run_manager import RunManager


class _FakeSink:
    """Minimal sink for testing."""

    async def on_event(self, event: Any) -> None:
        pass


class TestRunManager:
    def test_register_and_deregister(self) -> None:
        rm = RunManager()
        run_id = uuid4()
        scheduler = MagicMock()

        rm.register_run(run_id, scheduler)
        assert rm.is_active(run_id)

        rm.deregister_run(run_id)
        assert not rm.is_active(run_id)

    def test_deregister_nonexistent_is_noop(self) -> None:
        rm = RunManager()
        rm.deregister_run(uuid4())  # Should not raise

    def test_subscribe_to_active_run_returns_unsubscribe(self) -> None:
        rm = RunManager()
        run_id = uuid4()
        scheduler = MagicMock()
        rm.register_run(run_id, scheduler)

        t_sink = _FakeSink()
        o_sink = _FakeSink()
        unsub = rm.subscribe(run_id, t_sink, o_sink)

        assert unsub is not None
        scheduler.add_transport_sink.assert_called_once_with(t_sink)
        scheduler.add_overseer_sink.assert_called_once_with(o_sink)

    def test_subscribe_after_completion_returns_none(self) -> None:
        """subscribe returns None when the run is not active."""
        rm = RunManager()
        run_id = uuid4()

        result = rm.subscribe(run_id, _FakeSink(), _FakeSink())
        assert result is None

    def test_unsubscribe_closure_calls_remove(self) -> None:
        rm = RunManager()
        run_id = uuid4()
        scheduler = MagicMock()
        rm.register_run(run_id, scheduler)

        t_sink = _FakeSink()
        o_sink = _FakeSink()
        unsub = rm.subscribe(run_id, t_sink, o_sink)
        assert unsub is not None

        unsub()

        scheduler.remove_transport_sink.assert_called_once_with(t_sink)
        scheduler.remove_overseer_sink.assert_called_once_with(o_sink)

    def test_active_run_ids(self) -> None:
        rm = RunManager()
        id1 = uuid4()
        id2 = uuid4()
        rm.register_run(id1, MagicMock())
        rm.register_run(id2, MagicMock())

        assert rm.active_run_ids() == frozenset({id1, id2})

        rm.deregister_run(id1)
        assert rm.active_run_ids() == frozenset({id2})
