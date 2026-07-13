"""PostgreSQL integration tests.

These tests exercise the trace module against a real PostgreSQL database
via testcontainers. They skip gracefully when docker is unavailable.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from orxtra.identity import PgPrincipalStorage
from orxtra.protocols import (
    KIND_RUN,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
)
from orxtra.trace import (
    InvalidTransitionError,
    TraceWriter,
    acquire_run_lock,
    read_active_constraints,
    read_run_report,
    release_run_lock,
)

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import asyncpg

pytestmark = skip_no_docker


# -- Helpers ------------------------------------------------------------------


async def _create_run(writer: TraceWriter, pool: asyncpg.Pool) -> uuid6.UUID:
    """Create a run and transition to running.

    runs.created_by FKs into principals, so seed the singleton system
    principal (idempotent) as the creator and mint the run's own principal
    before inserting the row.
    """
    storage = PgPrincipalStorage(pool)
    creator = await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
    )
    run_id = uuid6.uuid7()
    await storage.mint_principal(KIND_RUN, run_id, None)
    await writer.create_run(
        intent="test intent",
        config={"key": "value"},
        autonomy_level="full",
        run_id=run_id,
        created_by=creator.id,
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
        task_type="callable",
    )


# -- Schema creation ----------------------------------------------------------


class TestSchemaCreation:
    """Verify the schema DDL runs cleanly against a real PG instance."""

    async def test_all_tables_created(self, pg_pool: asyncpg.Pool) -> None:
        """All expected tables exist after schema creation."""
        from _generated.tables_auth import (
            TABLE_NAMES as AUTH_TABLES,
        )
        from _generated.tables_dispatch import (
            TABLE_NAMES as DISPATCH_TABLES,
        )
        from _generated.tables_trace import (
            TABLE_NAMES as TRACE_TABLES,
        )

        rows = await pg_pool.fetch(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        )
        actual = {row["tablename"] for row in rows}
        expected = set(TRACE_TABLES) | set(DISPATCH_TABLES) | set(AUTH_TABLES)
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
        run_id = await _create_run(writer, pg_pool)

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
        run_id = await _create_run(writer, pg_pool)
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
        run_id = await _create_run(writer, pg_pool)
        task_id = await _create_task(writer, run_id)

        # created -> active is not valid (must go through prechecking)
        with pytest.raises(InvalidTransitionError):
            await writer.transition_task(task_id, "active")

    async def test_terminal_state_transition_raises(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Cannot transition from a terminal state."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)
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
        run_id = await _create_run(writer, pg_pool)

        event_id, inserted = await writer.write_event(
            run_id=run_id,
            event_type="test_event",
            data={"foo": "bar"},
        )
        assert inserted is True

        row = await pg_pool.fetchrow(
            "SELECT event_type, data FROM events WHERE id = $1", event_id
        )
        assert row is not None
        assert row["event_type"] == "test_event"
        assert json.loads(row["data"]) == {"foo": "bar"}


# -- Run identity at birth (E2E: start_run mints + attributes) ----------------


class TestStartRunIdentity:
    """The run identity vertical, end to end against a real database.

    Mirrors the CLI ``run start`` path: a SYSTEM-tier operator context resolves
    to the seeded system principal, ``start_run`` generates the run id, mints the
    run's own principal, and persists the run attributed to the caller. The
    scheduler and definition loaders are stubbed so the test exercises only the
    identity/persistence seam, not workflow execution.
    """

    async def test_start_run_mints_run_principal_and_attributes_creator(
        self, pg_pool: asyncpg.Pool, tmp_path: Path,
    ) -> None:
        from datetime import UTC, datetime
        from decimal import Decimal
        from unittest.mock import AsyncMock, MagicMock, patch

        from orxtra.identity import PgPrincipalStorage, resolve_caller_principal
        from orxtra.protocols import (
            ALL_SCOPES,
            KIND_RUN,
            KIND_SYSTEM,
            SYSTEM_PRINCIPAL_EXTERNAL_REF,
            AuthContext,
            TrustTier,
        )
        from orxtra.services import RunConfig, start_run

        # Seed the singleton system principal (as db init / api lifespan do).
        storage = PgPrincipalStorage(pg_pool)
        system_principal = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )

        # A SYSTEM-tier operator context resolves to the system principal --
        # exactly what the CLI operator does on every dispatch.
        operator_ctx = AuthContext(
            id=uuid6.uuid7(),
            consumer_id=None,
            scopes=ALL_SCOPES,
            trust_tier=TrustTier.SYSTEM,
            authenticated_via="test-operator",
            issued_at=datetime.now(UTC),
            expires_at=None,
        )
        caller_principal = await resolve_caller_principal(operator_ctx, storage)
        assert caller_principal.id == system_principal.id

        config = RunConfig(
            workflow_path=tmp_path / "workflow.toml",
            agents_dir=tmp_path / "agents",
            knowledge_dir=tmp_path / "knowledge",
            categories_path=tmp_path / "cats.toml",
            read_root=tmp_path,
            db_url="postgres://localhost/test",
            provider_configs={},
            budget=Decimal("1.00"),
            autonomy_level="medium",
        )

        # Stub the definition loaders and scheduler so start_run reaches only
        # the mint + create_run + transition seam.
        with (
            patch("orxtra.services._run.load_agents", return_value={}),
            patch("orxtra.services._run.load_categories", return_value={}),
            patch("orxtra.services._run.load_workflow", return_value=MagicMock()),
            patch(
                "orxtra.services._run.load_knowledge_files",
                new_callable=AsyncMock,
            ),
            patch("orxtra.services._run.Scheduler") as mock_sched_cls,
        ):
            mock_sched = AsyncMock()
            mock_sched.execute_workflow = AsyncMock()
            mock_sched_cls.return_value = mock_sched

            run_id = await start_run(
                pg_pool,
                storage,
                caller_principal,
                "e2e identity intent",
                config,
            )

        # The persisted run is attributed to the system principal.
        run_row = await pg_pool.fetchrow(
            "SELECT created_by FROM runs WHERE id = $1", run_id,
        )
        assert run_row is not None
        assert run_row["created_by"] == system_principal.id

        # A run principal was minted, sharing the run's id as its external_ref.
        run_principal = await storage.get_principal_by_ref(KIND_RUN, run_id)
        assert run_principal is not None
        assert run_principal.kind == KIND_RUN
        assert run_principal.external_ref == run_id
        assert run_principal.display_name is None

        # read_run_report surfaces the creating actor.
        report = await read_run_report(pg_pool, run_id)
        assert report is not None
        assert report.created_by == system_principal.id


# -- LISTEN/NOTIFY ------------------------------------------------------------


class TestListenNotify:
    """Verify the event trigger fires pg_notify on event insert."""

    async def test_write_event_fires_notify(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Inserting an event fires LISTEN/NOTIFY on 'orxtra_events'."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)

        notifications: list[dict[str, Any]] = []

        def _on_notify(
            conn: object, pid: int, channel: str, payload: str
        ) -> None:
            notifications.append(json.loads(payload))

        # Set up LISTEN on a dedicated connection
        listen_conn = await pg_pool.acquire()
        try:
            await listen_conn.add_listener("orxtra_events", _on_notify)

            # Write an event (on a different connection via the pool)
            await writer.write_event(
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
            await listen_conn.remove_listener("orxtra_events", _on_notify)
            await pg_pool.release(listen_conn)


# -- Advisory locks ------------------------------------------------------------


class TestAdvisoryLocks:
    """Verify advisory lock acquire/release semantics."""

    async def test_acquire_succeeds(self, pg_pool: asyncpg.Pool) -> None:
        """First acquire on a run_id succeeds."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)

        await acquire_run_lock(pg_pool, run_id)
        await release_run_lock(pg_pool, run_id)

    async def test_second_acquire_fails(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Second acquire on the same run_id from a different connection.

        Raises RunLockError. Advisory locks are per-connection. We hold
        the lock on one connection and attempt acquisition from a
        different one.
        """
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)

        from orxtra.trace import lock_key

        key = lock_key(run_id)

        # Hold the lock on a dedicated connection
        conn1 = await pg_pool.acquire()
        try:
            acquired = await conn1.fetchval(
                "SELECT pg_try_advisory_lock($1)", key
            )
            assert acquired is True

            # Second acquire from a DIFFERENT connection must fail
            conn2 = await pg_pool.acquire()
            try:
                acquired2 = await conn2.fetchval(
                    "SELECT pg_try_advisory_lock($1)", key
                )
                assert acquired2 is False
            finally:
                await pg_pool.release(conn2)
        finally:
            await conn1.fetchval("SELECT pg_advisory_unlock($1)", key)
            await pg_pool.release(conn1)


# -- Constraints round-trip ----------------------------------------------------


class TestConstraints:
    """Verify write_constraint + read_active_constraints."""

    async def test_constraint_round_trip(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """write_constraint + read_active_constraints round-trips."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)

        c_id = await writer.write_constraint(
            run_id=run_id,
            text="No external API calls",
            tier="mechanical",
            kind="prohibition",
            args={"scope": "all"},
        )

        constraints = await read_active_constraints(pg_pool, run_id)
        assert len(constraints) >= 1
        match = [c for c in constraints if c["id"] == c_id]
        assert len(match) == 1
        assert match[0]["text"] == "No external API calls"
        assert match[0]["tier"] == "mechanical"
        assert match[0]["kind"] == "prohibition"


# -- Auth table round-trip -----------------------------------------------------


class TestAuthTables:
    """Verify auth tables (consumers, credentials) round-trip correctly."""

    async def test_consumer_credential_round_trip(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Insert a consumer + credential and read them back."""
        async with pg_pool.acquire() as conn:
            # Insert a consumer
            consumer_id = await conn.fetchval(
                """
                INSERT INTO consumers (name, trust_tier, scope_grants)
                VALUES ($1, $2, $3::jsonb)
                RETURNING id
                """,
                "test-consumer",
                "verified",
                '["events:read", "events:write"]',
            )
            assert consumer_id is not None

            # Insert a credential linked to the consumer
            cred_id = await conn.fetchval(
                """
                INSERT INTO credentials
                    (consumer_id, credential_type,
                     credential_hash, algorithm)
                VALUES ($1, $2, $3, $4)
                RETURNING id
                """,
                consumer_id,
                "api_key",
                "sha256_hash_placeholder",
                "sha256",
            )
            assert cred_id is not None

            # Read back consumer
            consumer = await conn.fetchrow(
                "SELECT name, trust_tier FROM consumers WHERE id = $1",
                consumer_id,
            )
            assert consumer is not None
            assert consumer["name"] == "test-consumer"
            assert consumer["trust_tier"] == "verified"

            # Read back credential
            cred = await conn.fetchrow(
                "SELECT credential_type, credential_hash "
                "FROM credentials WHERE id = $1",
                cred_id,
            )
            assert cred is not None
            assert cred["credential_type"] == "api_key"
            assert cred["credential_hash"] == "sha256_hash_placeholder"

            # Verify FK cascade: deleting consumer cascades to credential
            await conn.execute(
                "DELETE FROM consumers WHERE id = $1", consumer_id
            )
            remaining = await conn.fetchval(
                "SELECT count(*) FROM credentials WHERE id = $1", cred_id
            )
            assert remaining == 0
