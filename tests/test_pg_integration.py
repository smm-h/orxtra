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
    run_principal = await storage.mint_principal(KIND_RUN, run_id, None)
    await writer.create_run(
        intent="test intent",
        config={"key": "value"},
        autonomy_level="full",
        run_id=run_id,
        created_by=creator.id,
    )
    await writer.transition_run(
        run_id, "running", principal_id=run_principal.id,
    )
    return run_id


async def _run_pid(pool: asyncpg.Pool, run_id: uuid6.UUID) -> uuid6.UUID:
    """Resolve a run's own principal id (minted at run birth) for attribution."""
    storage = PgPrincipalStorage(pool)
    principal = await storage.get_principal_by_ref(KIND_RUN, run_id)
    assert principal is not None
    return principal.id


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
        await writer.transition_task(task_id, "prechecking", principal_id=await _run_pid(pg_pool, run_id))
        await writer.transition_task(task_id, "active", principal_id=await _run_pid(pg_pool, run_id))
        await writer.transition_task(task_id, "postchecking", principal_id=await _run_pid(pg_pool, run_id))
        await writer.transition_task(task_id, "completed", principal_id=await _run_pid(pg_pool, run_id))

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
            await writer.transition_task(task_id, "active", principal_id=await _run_pid(pg_pool, run_id))

    async def test_terminal_state_transition_raises(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """Cannot transition from a terminal state."""
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)
        task_id = await _create_task(writer, run_id)

        # Walk to completed (terminal)
        await writer.transition_task(task_id, "prechecking", principal_id=await _run_pid(pg_pool, run_id))
        await writer.transition_task(task_id, "active", principal_id=await _run_pid(pg_pool, run_id))
        await writer.transition_task(task_id, "postchecking", principal_id=await _run_pid(pg_pool, run_id))
        await writer.transition_task(task_id, "completed", principal_id=await _run_pid(pg_pool, run_id))

        with pytest.raises(InvalidTransitionError):
            await writer.transition_task(task_id, "active", principal_id=await _run_pid(pg_pool, run_id))

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
            principal_id=await _run_pid(pg_pool, run_id),
        )
        assert inserted is True

        row = await pg_pool.fetchrow(
            "SELECT event_type, data FROM events WHERE id = $1", event_id
        )
        assert row is not None
        assert row["event_type"] == "test_event"
        assert json.loads(row["data"]) == {"foo": "bar"}

    async def test_write_event_idempotency_returns_existing_id(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        """On idempotency-key conflict, the EXISTING row id is returned.

        Cross-backend parity with InMemoryBackend's ``(existing_id, False)``:
        the second write must NOT return a freshly generated, never-persisted
        uuid -- it returns the id of the event that actually stored the key.
        """
        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)
        pid = await _run_pid(pg_pool, run_id)

        id1, inserted1 = await writer.write_event(
            run_id=run_id,
            event_type="dedup_event",
            data={"n": 1},
            principal_id=pid,
            idempotency_key="pg-dedup-key",
        )
        assert inserted1 is True

        id2, inserted2 = await writer.write_event(
            run_id=run_id,
            event_type="dedup_event",
            data={"n": 2},
            principal_id=pid,
            idempotency_key="pg-dedup-key",
        )
        assert inserted2 is False
        # The returned id is the EXISTING persisted row, not a fresh uuid.
        assert id2 == id1
        stored = await pg_pool.fetchrow(
            "SELECT id FROM events WHERE id = $1", id2,
        )
        assert stored is not None, "returned id must reference a persisted row"

        # Only one event was stored for the key; the duplicate never inserted.
        count = await pg_pool.fetchval(
            "SELECT count(*) FROM events WHERE idempotency_key = $1",
            "pg-dedup-key",
        )
        assert count == 1


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


class TestCreateSourceIdentity:
    """The source identity vertical, end to end through the dispatcher.

    A SYSTEM-tier operator context resolves to the seeded system principal;
    dispatching ``create_source`` mints the source's own principal (kind=source,
    display_name=slug) and persists the source attributed to the caller. An
    unknown ``credential_id`` is rejected at the dispatch choke point.
    """

    async def test_create_source_mints_principal_and_attributes_creator(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        from datetime import UTC, datetime
        from uuid import uuid4

        from orxtra.dispatch import PgDispatchBackend
        from orxtra.identity import PgPrincipalStorage
        from orxtra.protocols import (
            ALL_SCOPES,
            KIND_SOURCE,
            SYSTEM_PRINCIPAL_EXTERNAL_REF,
            AuthContext,
            TrustTier,
        )
        from orxtra.services import DispatchContext, dispatch

        storage = PgPrincipalStorage(pg_pool)
        system_principal = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )

        operator_ctx = AuthContext(
            id=uuid6.uuid7(),
            consumer_id=None,
            scopes=ALL_SCOPES,
            trust_tier=TrustTier.SYSTEM,
            authenticated_via="test-operator",
            issued_at=datetime.now(UTC),
            expires_at=None,
        )
        context = DispatchContext(
            pool=pg_pool,
            dispatch_backend=PgDispatchBackend(pg_pool),
            principal_storage=storage,
            auth_context=operator_ctx,
        )

        source_id = await dispatch(
            context,
            "create_source",
            {"slug": "gh-e2e", "name": "GitHub E2E"},
        )

        # The persisted source is attributed to the system principal.
        source_row = await pg_pool.fetchrow(
            "SELECT created_by FROM sources WHERE id = $1", source_id,
        )
        assert source_row is not None
        assert source_row["created_by"] == system_principal.id

        # A source principal was minted, sharing the source's id as external_ref
        # and carrying the slug as its display_name.
        source_principal = await storage.get_principal_by_ref(
            KIND_SOURCE, source_id,
        )
        assert source_principal is not None
        assert source_principal.kind == KIND_SOURCE
        assert source_principal.external_ref == source_id
        assert source_principal.display_name == "gh-e2e"

        # An unknown credential_id is a hard error at dispatch time.
        with pytest.raises(ValueError, match="Unknown credential_id"):
            await dispatch(
                context,
                "create_source",
                {
                    "slug": "bad-cred",
                    "name": "Bad Cred",
                    "credential_id": str(uuid4()),
                },
            )


# -- Inbox bulk expiry ---------------------------------------------------------


class TestExpireDueInboxItemsPg:
    """Verify expire_due_inbox_items against a real PG database."""

    async def test_bulk_expiry_round_trip(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """expire_due_inbox_items expires pending items with past deadlines."""
        from datetime import UTC, datetime, timedelta

        from orxtra.trace import read_inbox

        writer = TraceWriter(pg_pool)
        run_id = await _create_run(writer, pg_pool)
        now = datetime.now(UTC)
        past = now - timedelta(hours=1)
        future = now + timedelta(hours=1)

        # Pending + past deadline -> should expire
        await writer.create_inbox_item(
            run_id, "choice", "q1", [], None, None, None,
            tags=[], deadline=past,
        )
        # Pending + no deadline -> untouched
        await writer.create_inbox_item(
            run_id, "choice", "q2", [], None, None, None, tags=[],
        )
        # Pending + future deadline -> untouched
        await writer.create_inbox_item(
            run_id, "choice", "q3", [], None, None, None,
            tags=[], deadline=future,
        )
        # Answered + past deadline -> untouched
        item4 = await writer.create_inbox_item(
            run_id, "choice", "q4", [], None, None, None,
            tags=[], deadline=past,
        )
        pid = await _run_pid(pg_pool, run_id)
        await writer.answer_inbox_item(item4, "yes", resolved_by=pid)

        count = await writer.expire_due_inbox_items(now)
        assert count == 1

        items = await read_inbox(pg_pool, run_id, status="expired")
        assert len(items) == 1
        assert items[0].question == "q1"
        assert items[0].resolved_by is not None

        pending = await read_inbox(pg_pool, run_id, status="pending")
        assert len(pending) == 2


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
                principal_id=await _run_pid(pg_pool, run_id),
            )

            # Give PG a moment to deliver the notification
            await asyncio.sleep(0.5)

            assert len(notifications) >= 1
            payload = notifications[-1]
            assert payload["event_type"] == "notify_test"
            assert payload["run_id"] == str(run_id)
            # The NOTIFY payload carries principal_id (cast to text), not source.
            assert "source" not in payload
            assert payload["principal_id"] == str(
                await _run_pid(pg_pool, run_id),
            )
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
            # consumers.principal_id FKs into principals (NOT NULL), so mint the
            # consumer's own principal first, then reference it.
            principal_id = await conn.fetchval(
                """
                INSERT INTO principals (kind, external_ref, display_name)
                VALUES ('consumer', gen_random_uuid(), 'test-consumer')
                RETURNING id
                """,
            )
            # Insert a consumer
            consumer_id = await conn.fetchval(
                """
                INSERT INTO consumers (principal_id, name, trust_tier, scope_grants)
                VALUES ($1, $2, $3, $4::jsonb)
                RETURNING id
                """,
                principal_id,
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


# -- Subscription owner-filter parity -----------------------------------------


class TestListSubscriptionsOwnerParity:
    """The PG and in-memory backends filter subscriptions identically.

    Owner filtering (principal_id) and enabled_only must produce the same
    result set on both backends for an identical corpus of subscriptions.
    """

    async def test_owner_filter_parity(self, pg_pool: asyncpg.Pool) -> None:
        from orxtra.dispatch import (
            FilterPredicate,
            InMemoryDispatchBackend,
            PgDispatchBackend,
        )
        from orxtra.identity import PgPrincipalStorage
        from orxtra.protocols import KIND_CONSUMER, Subscription

        storage = PgPrincipalStorage(pg_pool)
        # Two distinct owning principals (PG enforces the FK; the in-memory
        # backend does not, but reuses the same ids so results are comparable).
        owner_a = await storage.mint_principal(
            KIND_CONSUMER, uuid6.uuid7(), "owner-a",
        )
        owner_b = await storage.mint_principal(
            KIND_CONSUMER, uuid6.uuid7(), "owner-b",
        )

        # Corpus: A owns two (one disabled), B owns one (enabled).
        corpus = [
            Subscription(
                id=uuid6.uuid7(), filter=FilterPredicate(),
                enabled=True, principal_id=owner_a.id,
            ),
            Subscription(
                id=uuid6.uuid7(), filter=FilterPredicate(),
                enabled=False, principal_id=owner_a.id,
            ),
            Subscription(
                id=uuid6.uuid7(), filter=FilterPredicate(),
                enabled=True, principal_id=owner_b.id,
            ),
        ]

        pg_backend = PgDispatchBackend(pg_pool)
        mem_backend = InMemoryDispatchBackend()
        for sub in corpus:
            await pg_backend.create_subscription(sub)
            await mem_backend.create_subscription(sub)

        async def _ids(
            backend: Any, *, enabled_only: bool, principal_id: Any,
        ) -> set[Any]:
            subs = await backend.list_subscriptions(
                enabled_only=enabled_only, principal_id=principal_id,
            )
            return {s.id for s in subs}

        # Matrix of filters -- every combination must agree across backends.
        for enabled_only in (True, False):
            for principal_id in (None, owner_a.id, owner_b.id):
                pg_ids = await _ids(
                    pg_backend,
                    enabled_only=enabled_only,
                    principal_id=principal_id,
                )
                mem_ids = await _ids(
                    mem_backend,
                    enabled_only=enabled_only,
                    principal_id=principal_id,
                )
                assert pg_ids == mem_ids, (
                    f"backend divergence for enabled_only={enabled_only}, "
                    f"principal_id={principal_id}: pg={pg_ids} mem={mem_ids}"
                )

        # Spot-check the expected shape via one backend.
        assert len(await _ids(
            pg_backend, enabled_only=False, principal_id=owner_a.id,
        )) == 2
        assert len(await _ids(
            pg_backend, enabled_only=True, principal_id=owner_a.id,
        )) == 1
        assert len(await _ids(
            pg_backend, enabled_only=True, principal_id=owner_b.id,
        )) == 1
