"""E2E proof of the AG-UI streaming pipeline.

Verifies the AG-UI server's SSE transport seams without needing a live
scheduler or a real PostgreSQL database:

1. **Subscribe-run seam**: transport and overseer events flow through
   the translator and sink pipeline, producing the correct AG-UI event
   sequence (TextMessageStart + TextMessageContent for text deltas,
   RunStartedEvent for overseer RunStarted).

2. **Completed-run snapshot**: ``_build_snapshot_from_report`` extracts
   task summaries, cost, and status from a RunReport, producing the
   enriched snapshot that the SSE handler sends to clients reconnecting
   to a finished run.

3. **Two concurrent clients**: two translators for the same run produce
   independent event sequences (different message IDs, independent step
   state), proving that per-SSE-connection translator isolation works.

4. **BroadcasterRegistry**: per-run channel isolation (different runs
   get different broadcasters; subscribe/unsubscribe lifecycle;
   terminal + empty eviction).
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

import uuid6
from ag_ui.core import (
    RunStartedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageStartEvent,
)
from orxtra.agui._registry import _BroadcasterRegistry
from orxtra.agui._server import _build_snapshot_from_report
from orxtra.agui._sinks import AGUIOverseerSink, AGUITransportSink
from orxtra.agui._translator import AGUITranslator
from orxtra.protocols import RunStarted
from orxtra.trace._types import RunReport, TaskSummary
from orxtra.transport._events import StepStart, StreamDelta

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_run_report(
    run_id: UUID,
    *,
    status: str = "completed",
) -> RunReport:
    """Build a minimal RunReport for testing."""
    return RunReport(
        id=run_id,
        intent="e2e test",
        status=status,
        created_at=datetime.now(UTC),
        finished_at=datetime.now(UTC),
        created_by=uuid6.uuid7(),
        autonomy_level="medium",
        config_snapshot={},
        total_input_tokens=100,
        total_output_tokens=50,
        total_reasoning_tokens=10,
        total_cache_read_tokens=0,
        total_cache_write_tokens=0,
        total_cost_usd=Decimal("0.01"),
        coherence_summary="all good",
        tasks=[
            TaskSummary(
                id=uuid6.uuid7(),
                name="root",
                status="completed",
                task_type="workflow",
                parent_task_id=None,
                attempt_count=1,
            ),
        ],
        decisions=[],
        constraints=[],
        assumptions=[],
    )


# ---------------------------------------------------------------------------
# 1. Subscribe-run seam: transport + overseer sinks
# ---------------------------------------------------------------------------


class TestSubscribeRunSeam:
    """Verify the subscribe_run seam delivers events through the sinks."""

    async def test_transport_sink_translates_stream_delta(self) -> None:
        """Transport events flow through the translator to the AG-UI sink."""
        received: list[Any] = []

        async def callback(event: Any) -> None:
            received.append(event)

        translator = AGUITranslator(
            thread_id="t1",
            run_id="r1",
            thinking_visibility="silent",
        )
        sink = AGUITransportSink(translator, callback)

        # Push a StreamDelta through the sink.
        await sink.on_event(StreamDelta(text="hello"))

        # Two events: TextMessageStart + TextMessageContent.
        assert len(received) == 2
        assert isinstance(received[0], TextMessageStartEvent)
        assert isinstance(received[1], TextMessageContentEvent)
        assert received[1].delta == "hello"

    async def test_overseer_sink_translates_run_started(self) -> None:
        """Overseer events flow through the translator to the AG-UI sink."""
        received: list[Any] = []

        async def callback(event: Any) -> None:
            received.append(event)

        translator = AGUITranslator(
            thread_id="t1",
            run_id="r1",
            thinking_visibility="silent",
        )
        sink = AGUIOverseerSink(translator, callback)

        await sink.on_event(RunStarted(intent="test", config_snapshot={}))

        assert len(received) == 1
        assert isinstance(received[0], RunStartedEvent)

    async def test_second_delta_no_start_event(self) -> None:
        """A second StreamDelta emits only TextMessageContent, not Start."""
        received: list[Any] = []

        async def callback(event: Any) -> None:
            received.append(event)

        translator = AGUITranslator(
            thread_id="t1",
            run_id="r1",
            thinking_visibility="silent",
        )
        sink = AGUITransportSink(translator, callback)

        await sink.on_event(StreamDelta(text="a"))
        await sink.on_event(StreamDelta(text="b"))

        # 3 events: Start + Content("a") + Content("b").
        assert len(received) == 3
        assert isinstance(received[0], TextMessageStartEvent)
        assert isinstance(received[1], TextMessageContentEvent)
        assert isinstance(received[2], TextMessageContentEvent)
        assert received[1].delta == "a"
        assert received[2].delta == "b"


# ---------------------------------------------------------------------------
# 2. Completed-run snapshot
# ---------------------------------------------------------------------------


class TestCompletedRunSnapshot:
    """Verify completed-run snapshot construction."""

    async def test_build_snapshot_from_report(self) -> None:
        """_build_snapshot_from_report extracts task summaries and cost."""
        run_id = uuid6.uuid7()
        report = _make_run_report(run_id)

        snapshot = _build_snapshot_from_report(report, str(run_id))
        assert snapshot["run_id"] == str(run_id)
        assert snapshot["status"] == "completed"
        assert len(snapshot["tasks"]) == 1
        assert snapshot["tasks"][0]["name"] == "root"
        assert snapshot["total_cost_usd"] == "0.01"
        assert snapshot["coherence_summary"] == "all good"

    async def test_failed_run_includes_status(self) -> None:
        """For a failed run, the snapshot includes the failure status."""
        run_id = uuid6.uuid7()
        report = _make_run_report(run_id, status="failed")
        snapshot = _build_snapshot_from_report(report, str(run_id))
        assert snapshot["status"] == "failed"

    async def test_aborted_run_includes_status(self) -> None:
        """For an aborted run, the snapshot includes the status."""
        run_id = uuid6.uuid7()
        report = _make_run_report(run_id, status="aborted")
        snapshot = _build_snapshot_from_report(report, str(run_id))
        assert snapshot["status"] == "aborted"


# ---------------------------------------------------------------------------
# 3. Two concurrent clients get independent event sequences
# ---------------------------------------------------------------------------


class TestIndependentTranslatorState:
    """Verify two concurrent clients have independent translator states."""

    async def test_two_translators_independent_message_ids(self) -> None:
        """Two translators for the same run produce different message IDs."""
        t1 = AGUITranslator(
            thread_id="t1", run_id="r1", thinking_visibility="silent",
        )
        t2 = AGUITranslator(
            thread_id="t2", run_id="r1", thinking_visibility="silent",
        )

        events1 = t1.translate_transport(StreamDelta(text="hello"))
        events2 = t2.translate_transport(StreamDelta(text="world"))

        starts1 = [e for e in events1 if isinstance(e, TextMessageStartEvent)]
        starts2 = [e for e in events2 if isinstance(e, TextMessageStartEvent)]

        assert len(starts1) == 1
        assert len(starts2) == 1

        # Message IDs are different (independent state).
        assert starts1[0].message_id != starts2[0].message_id

    async def test_two_translators_independent_step_state(self) -> None:
        """One translator's step state does not affect another."""
        t1 = AGUITranslator(
            thread_id="t1", run_id="r1", thinking_visibility="silent",
        )
        t2 = AGUITranslator(
            thread_id="t2", run_id="r1", thinking_visibility="silent",
        )

        events1 = t1.translate_transport(StepStart(session_id="s1"))
        events2 = t2.translate_transport(StepStart(session_id="s2"))

        steps1 = [e for e in events1 if isinstance(e, StepStartedEvent)]
        steps2 = [e for e in events2 if isinstance(e, StepStartedEvent)]

        assert len(steps1) == 1
        assert len(steps2) == 1
        assert steps1[0].step_name == "s1"
        assert steps2[0].step_name == "s2"


# ---------------------------------------------------------------------------
# 4. BroadcasterRegistry: per-run channel isolation
# ---------------------------------------------------------------------------


class TestBroadcasterRegistryIsolation:
    """Verify the per-run broadcaster registry provides channel isolation."""

    def test_different_runs_get_different_broadcasters(self) -> None:
        """Two run IDs get distinct broadcasters."""
        registry = _BroadcasterRegistry()
        run1 = uuid6.uuid7()
        run2 = uuid6.uuid7()

        b1 = registry.get_or_create(run1)
        b2 = registry.get_or_create(run2)

        assert b1 is not b2

    def test_same_run_gets_same_broadcaster(self) -> None:
        """Repeated calls for the same run ID return the same broadcaster."""
        registry = _BroadcasterRegistry()
        run_id = uuid6.uuid7()

        b1 = registry.get_or_create(run_id)
        b2 = registry.get_or_create(run_id)

        assert b1 is b2

    def test_subscribe_unsubscribe_lifecycle(self) -> None:
        """Subscribe increments count, unsubscribe decrements."""
        registry = _BroadcasterRegistry()
        run_id = uuid6.uuid7()

        registry.subscribe(run_id)
        assert registry.client_count(run_id) == 1

        registry.subscribe(run_id)
        assert registry.client_count(run_id) == 2

        registry.unsubscribe(run_id)
        assert registry.client_count(run_id) == 1

    def test_terminal_plus_empty_evicts(self) -> None:
        """A terminal channel with zero clients is evicted."""
        registry = _BroadcasterRegistry()
        run_id = uuid6.uuid7()

        registry.subscribe(run_id)
        registry.mark_terminal(run_id)
        # Still has 1 client, so not evicted.
        assert registry.has_channel(run_id)

        registry.unsubscribe(run_id)
        # Now terminal + empty => evicted.
        assert not registry.has_channel(run_id)
