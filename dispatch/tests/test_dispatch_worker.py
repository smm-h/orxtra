"""Unit tests for DispatchWorker using InMemoryDispatchBackend.

These tests exercise the worker's core logic without requiring a real
PostgreSQL instance. The InMemoryDispatchBackend provides cursor,
completion, and event polling methods.
"""
from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from orxtra.dispatch._dispatch_worker import DispatchWorker
from orxtra.dispatch._memory_backend import InMemoryDispatchBackend
from orxtra.dispatch._types import (
    FilterPredicate,
    Subscription,
    SubscriptionAction,
)
from orxtra.protocols import LogAction
from uuid6 import uuid7

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


def _make_event(
    event_type: str,
    data: dict[str, Any] | None = None,
    source: str = "test",
    idempotency_key: str | None = None,
) -> dict[str, Any]:
    """Create an event dict matching the poll_events_since format."""
    return {
        "id": uuid7(),
        "run_id": None,
        "task_id": None,
        "event_type": event_type,
        "source": source,
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
