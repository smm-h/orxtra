"""PostgreSQL integration tests.

These tests exercise the trace module against a real PostgreSQL database
via testcontainers. They skip gracefully when docker is unavailable.
"""
from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

import pytest
import uuid6

from orxt.trace import (
    InvalidTransitionError,
    TraceWriter,
    acquire_run_lock,
    read_active_constraints,
    read_run_report,
    release_run_lock,
    RunLockError,
)

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import asyncpg

pytestmark = [skip_no_docker, pytest.mark.timeout(60)]


# -- Helpers ------------------------------------------------------------------


async def _create_run(writer: TraceWriter) -> uuid6.UUID:
    """Create a run and transition to running."""
    run_id = await writer.create_run(
        intent="test intent",
        config={"key": "value"},
        autonomy_level="full",
    )
    await writer.transition_run(run_id, "running")
    return run_id


async def _create_task(
    writer: TraceWriter,
    run_id: uuid6.UUID,
    name: str = "test-task",
) -> uuid6.UUID:
    """Create a task under a run."""
    return await writer.create_task(
        run_id=run_id,
        parent_task_id=None,
        name=name,
        task_type="script",
    )


# -- Schema creation ----------------------------------------------------------


class TestSchemaCreation:
    """Verify the schema DDL runs cleanly against a real PG instance."""

    async def test_all_tables_created(self, pg_pool: asyncpg.Pool) -> None:
        """All expected tables exist after schema creation."""
        from orxt.trace._schema import TABLE_NAMES

        rows = await pg_pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        actual = {row["tablename"] for row in rows}
        # TABLE_NAMES covers 15 tables; run_heartbeats is the 16th
        expected = set(TABLE_NAMES.values()) | {"run_heartbeats"}
        missing = expected - actual
        assert not missing, f"Missing tables: {missing}"


# -- TraceWriter round-trips --------------------------------------------------


class TestTraceWriter:
    """Tests for TraceWriter persistence against real PG."""

    async def test_create_run_and_read_report(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """create_run persists; read_run_report returns it."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)

        report = await read_run_report(pg_pool, run_id)
        assert report is not None
        assert report.id == run_id
        assert report.intent == "test intent"
        assert report.status == "running"
        assert report.autonomy_level == "full"

    async def test_create_task_and_transition(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """create_task + transition_task respects state machine."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)
        task_id = await _create_task(writer, run_id)

        # Valid: created -> prechecking -> active -> postchecking -> completed
        await writer.transition_task(task_id, "prechecking")
        await writer.transition_task(task_id, "active")
        await writer.transition_task(task_id, "postchecking")
        await writer.transition_task(task_id, "completed")

        # Verify persisted state
        row = await pg_pool.fetchrow(
            "SELECT status FROM tasks WHERE id = $1", task_id
        )
        assert row is not None
        assert row["status"] == "completed"

    async def test_invalid_transition_raises(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Invalid task transition raises InvalidTransitionError."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)
        task_id = await _create_task(writer, run_id)

        # created -> active is not valid (must go through prechecking)
        with pytest.raises(InvalidTransitionError):
            await writer.transition_task(task_id, "active")

    async def test_terminal_state_transition_raises(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Cannot transition from a terminal state."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)
        task_id = await _create_task(writer, run_id)

        # Walk to completed (terminal)
        await writer.transition_task(task_id, "prechecking")
        await writer.transition_task(task_id, "active")
        await writer.transition_task(task_id, "postchecking")
        await writer.transition_task(task_id, "completed")

        with pytest.raises(InvalidTransitionError):
            await writer.transition_task(task_id, "active")

    async def test_write_event_persists(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """write_event inserts into events table."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)

        event_id = await writer.write_event(
            run_id=run_id,
            event_type="test_event",
            data={"foo": "bar"},
        )

        row = await pg_pool.fetchrow(
            "SELECT event_type, data FROM events WHERE id = $1", event_id
        )
        assert row is not None
        assert row["event_type"] == "test_event"
        assert json.loads(row["data"]) == {"foo": "bar"}


# -- LISTEN/NOTIFY ------------------------------------------------------------


class TestListenNotify:
    """Verify the event trigger fires pg_notify on event insert."""

    async def test_write_event_fires_notify(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Inserting an event fires LISTEN/NOTIFY on 'orxt_events'."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)

        notifications: list[dict[str, Any]] = []

        # Set up LISTEN on a dedicated connection
        listen_conn = await pg_pool.acquire()
        try:
            await listen_conn.add_listener(
                "orxt_events",
                lambda conn, pid, channel, payload: notifications.append(
                    json.loads(payload)
                ),
            )

            # Write an event (on a different connection via the pool)
            event_id = await writer.write_event(
                run_id=run_id,
                event_type="notify_test",
                data={"trigger": True},
            )

            # Give PG a moment to deliver the notification
            await asyncio.sleep(0.5)

            assert len(notifications) >= 1
            payload = notifications[-1]
            assert payload["event_type"] == "notify_test"
            assert payload["run_id"] == str(run_id)
        finally:
            await listen_conn.remove_listener("orxt_events", lambda *_: None)
            await pg_pool.release(listen_conn)


# -- Advisory locks ------------------------------------------------------------


class TestAdvisoryLocks:
    """Verify advisory lock acquire/release semantics."""

    async def test_acquire_succeeds(self, pg_pool: asyncpg.Pool) -> None:
        """First acquire on a run_id succeeds."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)

        await acquire_run_lock(pg_pool, run_id)
        await release_run_lock(pg_pool, run_id)

    async def test_second_acquire_fails(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Second acquire on the same run_id raises RunLockError."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)

        await acquire_run_lock(pg_pool, run_id)
        try:
            with pytest.raises(RunLockError):
                await acquire_run_lock(pg_pool, run_id)
        finally:
            await release_run_lock(pg_pool, run_id)


# -- Constraints round-trip ----------------------------------------------------


class TestConstraints:
    """Verify write_constraint + read_active_constraints."""

    async def test_constraint_round_trip(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """write_constraint + read_active_constraints round-trips."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer)

        c_id = await writer.write_constraint(
            run_id=run_id,
            text="No external API calls",
            tier="hard",
            kind="prohibition",
            args={"scope": "all"},
        )

        constraints = await read_active_constraints(pg_pool, run_id)
        assert len(constraints) >= 1
        match = [c for c in constraints if c["id"] == c_id]
        assert len(match) == 1
        assert match[0]["text"] == "No external API calls"
        assert match[0]["tier"] == "hard"
        assert match[0]["kind"] == "prohibition"
