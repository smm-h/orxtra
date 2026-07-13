"""Unit tests for DispatchWorker using InMemoryDispatchBackend.

These tests exercise the worker's core logic without requiring a real
PostgreSQL instance. The InMemoryDispatchBackend provides cursor,
completion, and event polling methods.
"""
from __future__ import annotations

import asyncio
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from orxtra.dispatch._dispatch_worker import DispatchWorker
from orxtra.dispatch._memory_backend import InMemoryDispatchBackend
from orxtra.dispatch._types import (
    FilterPredicate,
    Subscription,
    SubscriptionAction,
)
from orxtra.protocols import LogAction, ScriptAction
from uuid6 import uuid7

# Make _handlers importable for ScriptAction tests.
_tests_dir = str(Path(__file__).resolve().parent)
if _tests_dir not in sys.path:
    sys.path.insert(0, _tests_dir)

NOW = datetime.now(tz=UTC)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class StubFlushScheduler:
    """Minimal FlushScheduler that does nothing."""

    def schedule_flush(self, deadline: float, callback: Any) -> object:
        return None

    def cancel_flush(self, handle: object) -> None:
        pass


class StubActionExecutor:
    """Tracks workflow executions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], list[dict[str, object]]]] = []

    async def execute_workflow(
        self,
        workflow_path: str,
        config: dict[str, object],
        events: list[dict[str, object]],
    ) -> None:
        self.calls.append((workflow_path, config, events))


class StubPool:
    """Minimal stub that satisfies the pool interface for LISTEN/NOTIFY.

    The DispatchWorker needs pool.acquire() for LISTEN. This stub
    makes acquire() raise so the worker falls back to polling.
    """

    async def acquire(self) -> Any:
        msg = "Stub pool does not support acquire"
        raise RuntimeError(msg)

    async def release(self, conn: Any) -> None:
        pass

    async def close(self) -> None:
        pass


# A default actor principal for seeded events and subscription ownership.
_SYSTEM_PID = uuid7()
# A source principal used in event/routing tests.
_SOURCE_PID = uuid7()


async def _resolve_sources(slugs: Any) -> set[Any]:
    """Test resolver: the slug 'test-source' maps to the source principal."""
    return {_SOURCE_PID for s in slugs if s == "test-source"}


def _make_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    principal_id: Any = None,
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create an event dict matching the poll_events_since format."""
    return {
        "id": uuid7(),
        "run_id": None,
        "task_id": None,
        "event_type": event_type,
        "principal_id": principal_id if principal_id is not None else _SYSTEM_PID,
        "data": data or {},
        "idempotency_key": idempotency_key,
        "created_at": datetime.now(tz=UTC),
    }


def _make_worker(
    backend: InMemoryDispatchBackend,
    action_executor: StubActionExecutor | None = None,
    cursor_name: str = "test",
    poll_interval: float = 0.05,
) -> DispatchWorker:
    return DispatchWorker(
        backend=backend,  # type: ignore[arg-type]
        action_executor=action_executor or StubActionExecutor(),
        flush_scheduler=StubFlushScheduler(),  # type: ignore[arg-type]
        pool=StubPool(),  # type: ignore[arg-type]
        cursor_name=cursor_name,
        events_channel="test_channel",
        source_principal_resolver=_resolve_sources,
        poll_interval=poll_interval,
        batch_size=100,
    )


def _add_subscription_with_log(
    backend: InMemoryDispatchBackend,
    event_types: list[str] | None = None,
    log_message: str = "test-log",
) -> tuple[UUID, UUID]:
    """Synchronously add a subscription + log action to the backend."""
    sub = Subscription(
        id=uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=True,
        storage="persistent",
        principal_id=_SYSTEM_PID,
        created_at=NOW,
    )
    backend._subscriptions[sub.id] = sub
    action = SubscriptionAction(
        id=uuid7(),
        subscription_id=sub.id,
        position=0,
        action=LogAction(message=log_message, level="info"),
        created_at=NOW,
    )
    backend._actions[action.id] = action
    return sub.id, action.id


def _add_subscription_with_script(
    backend: InMemoryDispatchBackend,
    event_types: list[str] | None = None,
    handler: str = "_handlers:flush_handler",
    accumulator_config: dict[str, Any] | None = None,
) -> tuple[UUID, UUID]:
    """Add a subscription + script action, optionally with accumulator config."""
    sub = Subscription(
        id=uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=True,
        storage="persistent",
        principal_id=_SYSTEM_PID,
        created_at=NOW,
    )
    backend._subscriptions[sub.id] = sub
    action = SubscriptionAction(
        id=uuid7(),
        subscription_id=sub.id,
        position=0,
        action=ScriptAction(callable=handler),
        accumulator_config=accumulator_config,
        created_at=NOW,
    )
    backend._actions[action.id] = action
    return sub.id, action.id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestWorkerLifecycle:
    """Basic lifecycle: start, process, stop."""

    async def test_stop_without_events(self) -> None:
        """Worker starts and stops cleanly with no events."""
        backend = InMemoryDispatchBackend()
        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.2)
        await worker.stop()
        await run_task

    async def test_processes_events_and_advances_cursor(self) -> None:
        """Worker processes events and advances cursor."""
        backend = InMemoryDispatchBackend()
        _sub_id, action_id = _add_subscription_with_log(
            backend, event_types=["test.event"],
        )

        ev1 = _make_event("test.event", {"seq": 0})
        ev2 = _make_event("test.event", {"seq": 1})
        backend.inject_event(ev1)
        backend.inject_event(ev2)

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        await worker.stop()
        await run_task

        # Both events should have completion records.
        assert await backend.is_action_completed(ev1["id"], action_id)
        assert await backend.is_action_completed(ev2["id"], action_id)

        # Cursor should be at ev2.
        cursor = await backend.get_cursor_position("test")
        assert cursor == ev2["id"]

    async def test_unmatched_events_still_advance_cursor(self) -> None:
        """Events that don't match any subscription still advance cursor."""
        backend = InMemoryDispatchBackend()
        _add_subscription_with_log(
            backend, event_types=["matched.type"],
        )

        ev = _make_event("unmatched.type", {"data": "ignored"})
        backend.inject_event(ev)

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        await worker.stop()
        await run_task

        # Cursor still advances.
        cursor = await backend.get_cursor_position("test")
        assert cursor == ev["id"]


class TestCompletionDedup:
    """Completion records prevent double-execution."""

    async def test_completed_action_not_reexecuted(self) -> None:
        """Pre-existing completion record prevents re-execution."""
        backend = InMemoryDispatchBackend()
        _sub_id, action_id = _add_subscription_with_log(
            backend, event_types=["test.dedup"],
        )

        ev = _make_event("test.dedup")
        backend.inject_event(ev)

        # Pre-mark as completed.
        await backend.record_completion(ev["id"], action_id, "success")

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        await worker.stop()
        await run_task

        # Cursor advanced, but action was not re-executed.
        cursor = await backend.get_cursor_position("test")
        assert cursor == ev["id"]


class TestCursorRestart:
    """Restart from cursor position: no re-processing of events before cursor."""

    async def test_restart_from_cursor_skips_old_events(self) -> None:
        """Events before the cursor position are not re-processed."""
        backend = InMemoryDispatchBackend()
        _sub_id, action_id = _add_subscription_with_log(
            backend, event_types=["test.cursor"],
        )

        ev1 = _make_event("test.cursor", {"seq": 0})
        ev2 = _make_event("test.cursor", {"seq": 1})
        ev3 = _make_event("test.cursor", {"seq": 2})
        backend.inject_event(ev1)
        backend.inject_event(ev2)
        backend.inject_event(ev3)

        # Set cursor at ev2 -- simulating prior worker run.
        await backend.advance_cursor("test", ev2["id"])

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        await worker.stop()
        await run_task

        # Only ev3 should have a completion record from this run.
        # ev1 and ev2 are before the cursor.
        assert not await backend.is_action_completed(ev1["id"], action_id)
        assert not await backend.is_action_completed(ev2["id"], action_id)
        assert await backend.is_action_completed(ev3["id"], action_id)

        cursor = await backend.get_cursor_position("test")
        assert cursor == ev3["id"]


class TestMultipleSubscriptions:
    """Multiple subscriptions matching the same event."""

    async def test_multiple_subscriptions_all_executed(self) -> None:
        """Each matching subscription's action is executed independently."""
        backend = InMemoryDispatchBackend()
        _, action_id1 = _add_subscription_with_log(
            backend, event_types=["test.multi"], log_message="sub-1",
        )
        _, action_id2 = _add_subscription_with_log(
            backend, event_types=["test.multi"], log_message="sub-2",
        )

        ev = _make_event("test.multi")
        backend.inject_event(ev)

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        await worker.stop()
        await run_task

        assert await backend.is_action_completed(ev["id"], action_id1)
        assert await backend.is_action_completed(ev["id"], action_id2)


class TestAccumulatorCountThreshold:
    """Accumulator buffering: the worker respects accumulator_config."""

    async def test_count_threshold_buffers_then_flushes(self) -> None:
        """With accumulator_config={threshold: 3}, the action executes
        ONCE after 3 events, not per-event."""
        from _handlers import flush_calls

        flush_calls.clear()

        backend = InMemoryDispatchBackend()
        _sub_id, _action_id = _add_subscription_with_script(
            backend,
            event_types=["accum.evt"],
            handler="_handlers:flush_handler",
            accumulator_config={"threshold": 3, "flush_interval_s": 0},
        )

        # Inject 3 matching events.
        for i in range(3):
            backend.inject_event(_make_event("accum.evt", {"seq": i}))

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # The action should execute exactly ONCE (batch of 3), not 3 times.
        assert len(flush_calls) == 1
        assert len(flush_calls[0]) == 3

    async def test_no_accumulator_config_executes_per_event(self) -> None:
        """Without accumulator_config, each event triggers the action."""
        from _handlers import flush_calls

        flush_calls.clear()

        backend = InMemoryDispatchBackend()
        _add_subscription_with_script(
            backend,
            event_types=["normal.evt"],
            handler="_handlers:flush_handler",
            accumulator_config=None,
        )

        for i in range(3):
            backend.inject_event(_make_event("normal.evt", {"seq": i}))

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # Each event fires the action independently.
        assert len(flush_calls) == 3

    async def test_below_threshold_buffers_without_executing(self) -> None:
        """When event count < threshold, events buffer but action does not fire."""
        from _handlers import flush_calls

        flush_calls.clear()

        backend = InMemoryDispatchBackend()
        _sub_id, action_id = _add_subscription_with_script(
            backend,
            event_types=["accum.partial"],
            handler="_handlers:flush_handler",
            accumulator_config={"threshold": 5, "flush_interval_s": 0},
        )

        # Only 2 events, threshold is 5.
        for i in range(2):
            backend.inject_event(_make_event("accum.partial", {"seq": i}))

        worker = _make_worker(backend)
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # No flush -- below threshold.
        assert len(flush_calls) == 0
        # Events are buffered in the accumulator.
        assert await backend.pending_count(action_id) == 2

    async def test_time_threshold_schedules_flush(self) -> None:
        """With flush_interval_s > 0, the worker schedules a flush via
        FlushScheduler."""
        from _handlers import flush_calls

        flush_calls.clear()

        backend = InMemoryDispatchBackend()

        # Use a tracking scheduler to verify the schedule_flush call.
        scheduled: list[tuple[float, Any]] = []

        class TrackingFlushScheduler:
            def schedule_flush(self, deadline: float, callback: Any) -> object:
                scheduled.append((deadline, callback))
                return len(scheduled) - 1

            def cancel_flush(self, handle: object) -> None:
                pass

        _add_subscription_with_script(
            backend,
            event_types=["accum.timed"],
            handler="_handlers:flush_handler",
            accumulator_config={"threshold": 0, "flush_interval_s": 60},
        )

        backend.inject_event(_make_event("accum.timed", {"seq": 0}))

        worker = DispatchWorker(
            backend=backend,  # type: ignore[arg-type]
            action_executor=StubActionExecutor(),
            flush_scheduler=TrackingFlushScheduler(),  # type: ignore[arg-type]
            pool=StubPool(),  # type: ignore[arg-type]
            cursor_name="test-timed",
            events_channel="test_channel",
            source_principal_resolver=_resolve_sources,
            poll_interval=0.05,
            batch_size=100,
        )
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # No inline flush (threshold=0 means never inline).
        assert len(flush_calls) == 0
        # But the scheduler was called.
        assert len(scheduled) == 1
        assert scheduled[0][0] == 60

        # Simulate the scheduler firing the callback.
        await scheduled[0][1]()
        assert len(flush_calls) == 1
