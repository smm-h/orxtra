"""Tests for scheduler dispatching to overseer event sinks."""

from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path
from typing import Any

import uuid6
from orxtra.protocols import (
    OverseerEvent,
    RunStarted,
    StructuralAdvisory,
)
from orxtra.scheduler import Scheduler

_spec = _ilu.spec_from_file_location(
    "tests.shared_mocks",
    Path(__file__).resolve().parents[2] / "tests" / "shared_mocks.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
MockTraceWriter = _mod.MockTraceWriter
MockTransport = _mod.MockTransport


class RecordingSink:
    """EventSink that records all received events."""

    def __init__(self, *, fail: bool = False) -> None:
        self.events: list[OverseerEvent] = []
        self._fail = fail

    async def on_event(self, event: OverseerEvent) -> None:
        if self._fail:
            msg = "Sink failure"
            raise RuntimeError(msg)
        self.events.append(event)


def _make_scheduler(
    *,
    sinks: list[RecordingSink] | None = None,
    overseer_interface: Any = None,
) -> Scheduler:
    """Create a minimal Scheduler for testing sink dispatch."""
    run_id = uuid6.uuid7()
    trace_writer = MockTraceWriter()
    transport = MockTransport()
    return Scheduler(
        trace_writer=trace_writer,
        transport_registry={"test": transport},
        agents={},
        categories={},
        run_id=run_id,
        read_root=Path("/tmp/test"),
        autonomy_level="max",
        overseer_interface=overseer_interface,
        overseer_sinks=sinks or [],
    )


class TestOverseerSinkDispatch:
    async def test_headless_dispatches_to_sinks(self) -> None:
        """In headless mode, sinks still receive events."""
        sink = RecordingSink()
        scheduler = _make_scheduler(sinks=[sink])

        event = RunStarted(
            intent="test",
            config_snapshot={"name": "test"},
        )
        await scheduler._send_overseer_event(event)

        assert len(sink.events) == 1
        assert sink.events[0] is event

    async def test_multiple_sinks_all_receive(self) -> None:
        """All sinks receive the event."""
        sink_a = RecordingSink()
        sink_b = RecordingSink()
        scheduler = _make_scheduler(sinks=[sink_a, sink_b])

        event = RunStarted(
            intent="test",
            config_snapshot={},
        )
        await scheduler._send_overseer_event(event)

        assert len(sink_a.events) == 1
        assert len(sink_b.events) == 1

    async def test_failing_sink_does_not_block_others(self) -> None:
        """A failing sink does not prevent other sinks from receiving."""
        failing_sink = RecordingSink(fail=True)
        good_sink = RecordingSink()
        scheduler = _make_scheduler(sinks=[failing_sink, good_sink])

        event = RunStarted(
            intent="test",
            config_snapshot={},
        )
        # Should not raise
        await scheduler._send_overseer_event(event)

        assert len(good_sink.events) == 1

    async def test_no_sinks_works(self) -> None:
        """Scheduler works normally with no sinks."""
        scheduler = _make_scheduler()

        event = RunStarted(
            intent="test",
            config_snapshot={},
        )
        # Should not raise
        await scheduler._send_overseer_event(event)

    async def test_structural_advisory_dispatched(self) -> None:
        """StructuralAdvisory events are dispatched to sinks."""
        sink = RecordingSink()
        scheduler = _make_scheduler(sinks=[sink])

        event = StructuralAdvisory(
            task_id=uuid6.uuid7(),
            observation="test",
            suggestion="do something",
        )
        await scheduler._send_overseer_event(event)

        assert len(sink.events) == 1
        assert isinstance(sink.events[0], StructuralAdvisory)
