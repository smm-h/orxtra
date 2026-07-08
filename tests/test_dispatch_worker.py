"""PG integration tests for the DispatchWorker.

Tests exercise the full dispatch worker lifecycle against real PostgreSQL
via testcontainers:
- Graceful stop/restart: no loss, no double-execution
- Hard-kill simulation: at-least-once guarantee with completion records
- Duplicate idempotency-key events never reach an action
- Unmigrated DB: worker refuses with actionable message
"""
from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID

import pytest
from orxtra.dispatch import (
    DispatchWorker,
    FilterPredicate,
    PgDispatchBackend,
    Subscription,
    SubscriptionAction,
)
from orxtra.protocols import LogAction, ScriptAction
from orxtra.services import (
    AsyncioFlushScheduler,
    SchemaError,
    create_dispatch_worker,
    verify_schema,
)
from orxtra.trace import EVENTS_CHANNEL, TraceWriter
from uuid6 import uuid7

from tests.pg_fixtures import skip_no_docker

pytestmark = [skip_no_docker, pytest.mark.timeout(60)]


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class TrackingActionExecutor:
    """Records execute_workflow calls for test assertions."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], list[dict[str, object]]]] = []

    async def execute_workflow(
        self,
        workflow_path: str,
        config: dict[str, object],
        events: list[dict[str, object]],
    ) -> None:
        self.calls.append((workflow_path, config, events))


# Module-level tracking list for ScriptAction tests.
_script_invocations: list[dict[str, object]] = []


def dispatch_test_handler(events: list[dict[str, object]]) -> None:
    """ScriptAction handler that records invocations."""
    _script_invocations.extend(events)


async def _fire_event(
    pool: Any,
    event_type: str,
    data: dict[str, Any] | None = None,
    source: str = "test",
    run_id: UUID | None = None,
    idempotency_key: str | None = None,
) -> tuple[UUID, bool]:
    """Fire an event into the trace store."""
    writer = TraceWriter(pool)
    return await writer.write_event(
        run_id, event_type, data or {}, source=source,
        idempotency_key=idempotency_key,
    )


async def _create_subscription_with_log_action(
    backend: PgDispatchBackend,
    event_types: list[str] | None = None,
    log_message: str = "test-action",
) -> tuple[UUID, UUID]:
    """Create a subscription + log action, returning (sub_id, action_id)."""
    sub = Subscription(
        id=uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=True,
        storage="persistent",
    )
    await backend.create_subscription(sub)
    action = SubscriptionAction(
        id=uuid7(),
        subscription_id=sub.id,
        position=0,
        action=LogAction(message=log_message, level="info"),
    )
    await backend.create_action(action)
    return sub.id, action.id


async def _create_subscription_with_script_action(
    backend: PgDispatchBackend,
    event_types: list[str] | None = None,
) -> tuple[UUID, UUID]:
    """Create a subscription + script action, returning (sub_id, action_id)."""
    sub = Subscription(
        id=uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=True,
        storage="persistent",
    )
    await backend.create_subscription(sub)
    action = SubscriptionAction(
        id=uuid7(),
        subscription_id=sub.id,
        position=0,
        action=ScriptAction(
            callable="tests.test_dispatch_worker:dispatch_test_handler",
        ),
    )
    await backend.create_action(action)
    return sub.id, action.id


def _make_worker(
    pool: Any,
    backend: PgDispatchBackend,
    action_executor: TrackingActionExecutor | None = None,
    cursor_name: str = "test-cursor",
    poll_interval: float = 0.1,
) -> DispatchWorker:
    """Build a DispatchWorker with test-friendly settings."""
    return DispatchWorker(
        backend=backend,
        action_executor=action_executor or TrackingActionExecutor(),
        flush_scheduler=AsyncioFlushScheduler(),
        pool=pool,
        cursor_name=cursor_name,
        events_channel=EVENTS_CHANNEL,
        poll_interval=poll_interval,
        batch_size=100,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGracefulStopRestart:
    """Graceful stop/restart: no loss, no double-execution."""

    async def test_worker_processes_events_then_stops(
        self, pg_pool: Any,
    ) -> None:
        """Fire events, worker processes them, stop, no duplicates."""
        backend = PgDispatchBackend(pg_pool)
        _sub_id, action_id = await _create_subscription_with_log_action(
            backend, event_types=["test.graceful"],
        )

        # Fire 3 events.
        ev_ids = []
        for i in range(3):
            eid, _ = await _fire_event(
                pg_pool, "test.graceful", {"seq": i},
            )
            ev_ids.append(eid)

        worker = _make_worker(pg_pool, backend)

        # Run for a bit, then stop.
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # All 3 events should have completion records.
        for eid in ev_ids:
            assert await backend.is_action_completed(eid, action_id)

        # Cursor should be at the last event.
        cursor_pos = await backend.get_cursor_position("test-cursor")
        assert cursor_pos == ev_ids[-1]

    async def test_restart_after_graceful_stop_no_duplicates(
        self, pg_pool: Any,
    ) -> None:
        """Restart after graceful stop does not re-process completed events."""
        _script_invocations.clear()
        backend = PgDispatchBackend(pg_pool)
        _sub_id, action_id = await _create_subscription_with_script_action(
            backend, event_types=["test.restart"],
        )

        # Fire 2 events.
        _ev1, _ = await _fire_event(pg_pool, "test.restart", {"seq": 0})
        _ev2, _ = await _fire_event(pg_pool, "test.restart", {"seq": 1})

        # First run: process both events.
        worker1 = _make_worker(pg_pool, backend)
        run1 = asyncio.create_task(worker1.run())
        await asyncio.sleep(0.5)
        await worker1.stop()
        await run1

        count_after_first = len(_script_invocations)
        assert count_after_first == 2

        # Fire 1 more event.
        ev3, _ = await _fire_event(pg_pool, "test.restart", {"seq": 2})

        # Second run: should only process ev3.
        worker2 = _make_worker(pg_pool, backend)
        run2 = asyncio.create_task(worker2.run())
        await asyncio.sleep(0.5)
        await worker2.stop()
        await run2

        # Total should be 3 (2 from first run + 1 from second).
        assert len(_script_invocations) == 3

        # ev1 and ev2 were not re-processed.
        assert await backend.is_action_completed(ev3, action_id)


class TestHardKillSimulation:
    """Hard-kill mid-execution: no loss; interrupted action re-run or completed."""

    async def test_crash_mid_batch_reprocesses_uncompleted(
        self, pg_pool: Any,
    ) -> None:
        """Simulate crash: events without completion records get re-processed."""
        backend = PgDispatchBackend(pg_pool)
        _sub_id, action_id = await _create_subscription_with_log_action(
            backend, event_types=["test.crash"],
        )

        # Fire 3 events.
        ev1, _ = await _fire_event(pg_pool, "test.crash", {"seq": 0})
        ev2, _ = await _fire_event(pg_pool, "test.crash", {"seq": 1})
        ev3, _ = await _fire_event(pg_pool, "test.crash", {"seq": 2})

        # Simulate: worker processed ev1 (has completion) but crashed before
        # advancing cursor past ev2.
        await backend.record_completion(ev1, action_id, "success")
        await backend.advance_cursor("test-crash-cursor", ev1)

        # Restart worker from ev1 cursor. ev2 and ev3 should be processed.
        # ev1 is already past the cursor.
        worker = _make_worker(
            pg_pool, backend, cursor_name="test-crash-cursor",
        )
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # All events should now have completion records.
        assert await backend.is_action_completed(ev1, action_id)
        assert await backend.is_action_completed(ev2, action_id)
        assert await backend.is_action_completed(ev3, action_id)

        # Cursor at ev3.
        cursor_pos = await backend.get_cursor_position("test-crash-cursor")
        assert cursor_pos == ev3

    async def test_completed_event_not_reexecuted(
        self, pg_pool: Any,
    ) -> None:
        """Even if cursor is behind, completed events skip re-execution."""
        _script_invocations.clear()
        backend = PgDispatchBackend(pg_pool)
        _sub_id, action_id = await _create_subscription_with_script_action(
            backend, event_types=["test.dedup"],
        )

        # Fire event and manually mark completed.
        ev1, _ = await _fire_event(pg_pool, "test.dedup", {"data": "already-done"})
        await backend.record_completion(ev1, action_id, "success")
        # Do NOT advance cursor -- cursor is at beginning.

        worker = _make_worker(
            pg_pool, backend, cursor_name="test-dedup-cursor",
        )
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        # Action was NOT re-executed (completion record existed).
        dedup_invocations = [
            inv for inv in _script_invocations
            if inv.get("event_type") == "test.dedup"
        ]
        assert len(dedup_invocations) == 0

        # Cursor should have advanced past ev1 anyway.
        cursor_pos = await backend.get_cursor_position("test-dedup-cursor")
        assert cursor_pos == ev1


class TestIdempotencyDedup:
    """Duplicate idempotency-key events never reach any action."""

    async def test_duplicate_idempotency_key_no_double_dispatch(
        self, pg_pool: Any,
    ) -> None:
        """Idempotency key dedup at insert prevents double dispatch."""
        _script_invocations.clear()
        backend = PgDispatchBackend(pg_pool)
        _sub_id, _action_id = await _create_subscription_with_script_action(
            backend, event_types=["test.idemp"],
        )

        # Fire same idempotency key twice.
        _ev1, inserted1 = await _fire_event(
            pg_pool, "test.idemp", {"attempt": 1},
            idempotency_key="unique-key-123",
        )
        _ev2, inserted2 = await _fire_event(
            pg_pool, "test.idemp", {"attempt": 2},
            idempotency_key="unique-key-123",
        )

        # First insert succeeds, second is deduplicated.
        assert inserted1
        assert not inserted2

        # Worker processes only one event (the one that was actually stored).
        worker = _make_worker(
            pg_pool, backend, cursor_name="test-idemp-cursor",
        )
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.5)
        await worker.stop()
        await run_task

        idemp_invocations = [
            inv for inv in _script_invocations
            if inv.get("event_type") == "test.idemp"
        ]
        assert len(idemp_invocations) == 1


class TestUnmigratedDb:
    """Worker refuses to start on unmigrated DB with actionable message."""

    async def test_verify_schema_rejects_empty_db(
        self, pg_container: Any,
    ) -> None:
        """verify_schema raises SchemaError on empty DB -- the dispatcher
        worker calls verify_schema at startup via the CLI command."""
        import asyncpg

        url = pg_container.get_connection_url().replace(
            "postgresql+psycopg2://", "postgresql://",
        )
        pool = await asyncpg.create_pool(url)
        try:
            # Ensure empty schema.
            async with pool.acquire() as conn:
                await conn.execute("DROP SCHEMA public CASCADE")
                await conn.execute("CREATE SCHEMA public")

            with pytest.raises(SchemaError) as exc_info:
                await verify_schema(pool)

            msg = str(exc_info.value)
            assert "Database schema is incomplete" in msg
            assert "orxtra db init" in msg
        finally:
            await pool.close()


class TestServiceFactory:
    """The create_dispatch_worker factory constructs a working worker."""

    async def test_factory_creates_runnable_worker(
        self, pg_pool: Any,
    ) -> None:
        """create_dispatch_worker returns a DispatchWorker that can run/stop."""
        worker = create_dispatch_worker(
            pg_pool,
            cursor_name="factory-test",
            poll_interval=0.1,
        )
        assert isinstance(worker, DispatchWorker)

        # Verify it can start and stop cleanly.
        run_task = asyncio.create_task(worker.run())
        await asyncio.sleep(0.3)
        await worker.stop()
        await run_task
