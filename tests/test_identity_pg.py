"""PG round-trip tests for PgPrincipalStorage.

Exercises PgPrincipalStorage against a real PostgreSQL database via
testcontainers. Skips gracefully when docker is unavailable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
import uuid6
from orxtra.identity import PgPrincipalStorage, PrincipalInUseError
from orxtra.protocols import (
    ALL_SCOPES,
    KIND_CONSUMER,
    KIND_RUN,
    KIND_SOURCE,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    AuthContext,
    TrustTier,
)

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import asyncpg

pytestmark = skip_no_docker


class TestPrincipalCRUD:
    """Principal create, read, list, update, delete round-trips."""

    async def test_mint_and_get_principal(self, pg_pool: asyncpg.Pool) -> None:
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid4()
        minted = await storage.mint_principal(KIND_CONSUMER, ref, "alice")

        assert minted.kind == KIND_CONSUMER
        assert minted.external_ref == ref
        assert minted.display_name == "alice"
        assert minted.created_at is not None

        fetched = await storage.get_principal(minted.id)
        assert fetched is not None
        assert fetched.id == minted.id
        assert fetched.external_ref == ref

    async def test_get_principal_by_ref(self, pg_pool: asyncpg.Pool) -> None:
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid4()
        minted = await storage.mint_principal(KIND_CONSUMER, ref, "bob")

        by_ref = await storage.get_principal_by_ref(KIND_CONSUMER, ref)
        assert by_ref is not None
        assert by_ref.id == minted.id

        # Same ref under a different kind is a distinct (absent) actor.
        assert await storage.get_principal_by_ref(KIND_RUN, ref) is None

    async def test_get_nonexistent_principal(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        storage = PgPrincipalStorage(pg_pool)
        assert await storage.get_principal(uuid4()) is None

    async def test_mint_null_display_name(self, pg_pool: asyncpg.Pool) -> None:
        storage = PgPrincipalStorage(pg_pool)
        minted = await storage.mint_principal(KIND_RUN, uuid4(), None)
        assert minted.display_name is None
        fetched = await storage.get_principal(minted.id)
        assert fetched is not None
        assert fetched.display_name is None


class TestMintIdempotence:
    """mint_principal is idempotent on (kind, external_ref)."""

    async def test_repeated_mint_returns_same_row(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid4()
        first = await storage.mint_principal(KIND_CONSUMER, ref, "alice")
        second = await storage.mint_principal(KIND_CONSUMER, ref, "renamed")

        assert second.id == first.id
        # ON CONFLICT DO NOTHING: original display name is preserved.
        assert second.display_name == "alice"

        all_consumers = await storage.list_principals(KIND_CONSUMER)
        matching = [p for p in all_consumers if p.external_ref == ref]
        assert len(matching) == 1

    async def test_concurrent_double_mint(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """Two concurrent mints of the same (kind, ref) converge to one row."""
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid4()

        first, second = await asyncio.gather(
            storage.mint_principal(KIND_CONSUMER, ref, "racer-a"),
            storage.mint_principal(KIND_CONSUMER, ref, "racer-b"),
        )

        # Both callers get the same principal id.
        assert first.id == second.id

        # Exactly one row exists for that ref.
        rows = [
            p
            for p in await storage.list_principals(KIND_CONSUMER)
            if p.external_ref == ref
        ]
        assert len(rows) == 1


class TestListPrincipals:
    """list_principals filtering."""

    async def test_list_all_and_filter_by_kind(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        storage = PgPrincipalStorage(pg_pool)
        await storage.mint_principal(KIND_CONSUMER, uuid4(), "c1")
        await storage.mint_principal(KIND_CONSUMER, uuid4(), "c2")
        await storage.mint_principal(KIND_RUN, uuid4(), "r1")

        all_principals = await storage.list_principals()
        assert len(all_principals) >= 3

        consumers = await storage.list_principals(KIND_CONSUMER)
        assert len(consumers) == 2
        assert all(p.kind == KIND_CONSUMER for p in consumers)

        runs = await storage.list_principals(KIND_RUN)
        assert len(runs) == 1


class TestUpdateDisplayName:
    """update_display_name round-trip and absent-row hard error."""

    async def test_update_display_name(self, pg_pool: asyncpg.Pool) -> None:
        storage = PgPrincipalStorage(pg_pool)
        minted = await storage.mint_principal(KIND_CONSUMER, uuid4(), "old")

        await storage.update_display_name(minted.id, "new")

        fetched = await storage.get_principal(minted.id)
        assert fetched is not None
        assert fetched.display_name == "new"

    async def test_update_absent_principal_hard_error(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        storage = PgPrincipalStorage(pg_pool)
        with pytest.raises(KeyError):
            await storage.update_display_name(uuid4(), "ghost")


class TestDeletePrincipal:
    """delete_principal for an unreferenced principal."""

    async def test_delete_unreferenced_principal(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        storage = PgPrincipalStorage(pg_pool)
        minted = await storage.mint_principal(KIND_SYSTEM, uuid4(), "temp")

        await storage.delete_principal(minted.id)

        assert await storage.get_principal(minted.id) is None


def _consumer_ctx(consumer_id: uuid6.UUID) -> AuthContext:
    """Build an IDENTIFIED consumer auth context for the given consumer id."""
    return AuthContext(
        id=uuid6.uuid7(),
        consumer_id=consumer_id,
        scopes=ALL_SCOPES,
        trust_tier=TrustTier.IDENTIFIED,
        authenticated_via="test-consumer",
        issued_at=datetime.now(UTC),
        expires_at=None,
    )


class TestDeletePrincipalLifecycle:
    """delete_principal against real referencing FKs.

    A principal that anchors durable history (events, runs.created_by,
    sources.created_by, consumers.principal_id, inbox_items.resolved_by -- all
    five RESTRICT) is undeletable. A principal that only owns operational state
    (subscriptions -- CASCADE) is deletable, taking that state with it. A
    never-referenced principal deletes cleanly.
    """

    async def test_subscription_owner_deletes_and_cascades_via_dispatch(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(a) End-to-end: subscribe via a consumer dispatch context, list
        filtered by that principal, then delete the principal -> the owned
        subscription CASCADE-deletes and the principal is gone."""
        from orxtra.dispatch import PgDispatchBackend
        from orxtra.services import DispatchContext, dispatch

        storage = PgPrincipalStorage(pg_pool)
        # A consumer principal minted for the caller (no consumers row -- the
        # resolver only needs the minted principal; this keeps the owner
        # referenced ONLY by its subscription, so the delete CASCADEs).
        consumer_ref = uuid6.uuid7()
        owner = await storage.mint_principal(
            KIND_CONSUMER, consumer_ref, "cascade-consumer",
        )

        backend = PgDispatchBackend(pg_pool)
        ctx = DispatchContext(
            dispatch_backend=backend,
            principal_storage=storage,
            auth_context=_consumer_ctx(consumer_ref),
        )

        # Subscribe via dispatch -> the subscription is owned by the caller's
        # principal.
        sub_id = await dispatch(
            ctx,
            "subscribe",
            {
                "filter": {"event_types": ["e2e.owned"]},
                "actions": [{"message": "hi", "level": "info"}],
                "storage": "persistent",
            },
        )
        row = await pg_pool.fetchrow(
            "SELECT principal_id FROM subscriptions WHERE id = $1", sub_id,
        )
        assert row is not None
        assert row["principal_id"] == owner.id, (
            "a dispatched subscription must be owned by the caller's principal"
        )

        # Listing filtered by that principal returns the subscription.
        listed = await dispatch(
            ctx,
            "list_subscriptions",
            {"principal_id": str(owner.id)},
        )
        assert any(s.id == sub_id for s in listed), (
            "owner-filtered list must return the caller's subscription"
        )

        # Deleting the owner CASCADE-deletes its subscription; the principal
        # itself is gone.
        await storage.delete_principal(owner.id)
        assert await storage.get_principal(owner.id) is None
        gone = await pg_pool.fetchrow(
            "SELECT id FROM subscriptions WHERE id = $1", sub_id,
        )
        assert gone is None, (
            "deleting the owning principal must CASCADE-delete its subscription"
        )

    async def test_event_actor_is_undeletable(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(b) A principal that emitted an event is pinned by events.principal_id."""
        storage = PgPrincipalStorage(pg_pool)
        actor = await storage.mint_principal(KIND_CONSUMER, uuid6.uuid7(), "actor")
        await pg_pool.execute(
            "INSERT INTO events (event_type, principal_id) VALUES ($1, $2)",
            "history.happened", actor.id,
        )
        with pytest.raises(PrincipalInUseError) as exc_info:
            await storage.delete_principal(actor.id)
        assert str(actor.id) in str(exc_info.value), (
            "the error must name the undeletable principal id"
        )
        # The principal survives the refused delete.
        assert await storage.get_principal(actor.id) is not None

    async def test_run_creator_is_undeletable(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(c) The system principal that created a run is pinned by
        runs.created_by."""
        storage = PgPrincipalStorage(pg_pool)
        system = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        await pg_pool.execute(
            "INSERT INTO runs (intent, autonomy_level, created_by) "
            "VALUES ($1, $2, $3)",
            "a run", "medium", system.id,
        )
        with pytest.raises(PrincipalInUseError) as exc_info:
            await storage.delete_principal(system.id)
        assert str(system.id) in str(exc_info.value)

    async def test_source_creator_is_undeletable(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(d) A principal that created a source is pinned by
        sources.created_by."""
        storage = PgPrincipalStorage(pg_pool)
        creator = await storage.mint_principal(
            KIND_SOURCE, uuid6.uuid7(), "src-creator",
        )
        await pg_pool.execute(
            "INSERT INTO sources (slug, name, created_by) VALUES ($1, $2, $3)",
            "a-source", "A Source", creator.id,
        )
        with pytest.raises(PrincipalInUseError) as exc_info:
            await storage.delete_principal(creator.id)
        assert str(creator.id) in str(exc_info.value)

    async def test_consumer_own_principal_is_undeletable(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(e) A consumer's own backing principal is pinned by
        consumers.principal_id."""
        storage = PgPrincipalStorage(pg_pool)
        consumer_ref = uuid6.uuid7()
        principal = await storage.mint_principal(
            KIND_CONSUMER, consumer_ref, "acme",
        )
        await pg_pool.execute(
            "INSERT INTO consumers (id, name, trust_tier, principal_id) "
            "VALUES ($1, $2, $3, $4)",
            consumer_ref, "acme", "identified", principal.id,
        )
        with pytest.raises(PrincipalInUseError) as exc_info:
            await storage.delete_principal(principal.id)
        assert str(principal.id) in str(exc_info.value)

    async def test_inbox_resolver_is_undeletable(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(e2) A principal that resolved an inbox item is pinned by
        inbox_items.resolved_by -- the 5th RESTRICT FK.

        The item's run is created by a DIFFERENT (system) principal, so the
        resolver principal is referenced ONLY through resolved_by; the refused
        delete therefore isolates that single FK.
        """
        storage = PgPrincipalStorage(pg_pool)
        system = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        resolver = await storage.mint_principal(
            KIND_CONSUMER, uuid6.uuid7(), "inbox-resolver",
        )
        run_id = await pg_pool.fetchval(
            "INSERT INTO runs (intent, autonomy_level, created_by) "
            "VALUES ($1, $2, $3) RETURNING id",
            "inbox run", "medium", system.id,
        )
        await pg_pool.execute(
            "INSERT INTO inbox_items "
            "(run_id, status, decision_type, question, resolved_by) "
            "VALUES ($1, $2, $3, $4, $5)",
            run_id, "answered", "retry_strategy", "Proceed?", resolver.id,
        )
        with pytest.raises(PrincipalInUseError) as exc_info:
            await storage.delete_principal(resolver.id)
        assert str(resolver.id) in str(exc_info.value), (
            "the error must name the undeletable principal id"
        )
        # The principal survives the refused delete.
        assert await storage.get_principal(resolver.id) is not None

    async def test_never_acted_principal_deletes_cleanly(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(f) A principal referenced by nothing deletes cleanly."""
        storage = PgPrincipalStorage(pg_pool)
        idle = await storage.mint_principal(KIND_CONSUMER, uuid6.uuid7(), "idle")
        await storage.delete_principal(idle.id)
        assert await storage.get_principal(idle.id) is None


class TestSweepOrphanedRunPrincipals:
    """sweep_orphaned_run_principals against a real PG database.

    Covers the four axes: old orphan (deleted), fresh orphan (kept by age
    guard), orphan with history (kept by FK), and non-run kind (kept by
    kind scope).
    """

    async def test_old_orphan_is_swept(self, pg_pool: asyncpg.Pool) -> None:
        """A kind=run principal with no matching run and old created_at
        is deleted by the sweep."""
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid6.uuid7()
        minted = await storage.mint_principal(KIND_RUN, ref, None)
        # Backdate created_at to make it old enough.
        await pg_pool.execute(
            "UPDATE principals SET created_at = now() - interval '10 minutes'"
            " WHERE id = $1",
            minted.id,
        )
        swept = await storage.sweep_orphaned_run_principals(
            timedelta(minutes=5),
        )
        assert swept == 1
        assert await storage.get_principal(minted.id) is None

    async def test_fresh_orphan_is_kept(self, pg_pool: asyncpg.Pool) -> None:
        """A kind=run principal younger than the age guard is NOT swept."""
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid6.uuid7()
        minted = await storage.mint_principal(KIND_RUN, ref, None)
        # Principal was just created -- should NOT be swept.
        swept = await storage.sweep_orphaned_run_principals(
            timedelta(minutes=5),
        )
        assert swept == 0
        assert await storage.get_principal(minted.id) is not None

    async def test_run_principal_with_matching_run_is_kept(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """A kind=run principal whose external_ref matches a runs row
        is NOT swept, even if old."""
        storage = PgPrincipalStorage(pg_pool)
        # Seed a system principal to use as created_by.
        system = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        run_id = uuid6.uuid7()
        run_principal = await storage.mint_principal(KIND_RUN, run_id, None)
        # Create a matching run.
        await pg_pool.execute(
            "INSERT INTO runs (id, intent, autonomy_level, created_by)"
            " VALUES ($1, $2, $3, $4)",
            run_id, "test", "full", system.id,
        )
        # Backdate created_at.
        await pg_pool.execute(
            "UPDATE principals SET created_at = now() - interval '10 minutes'"
            " WHERE id = $1",
            run_principal.id,
        )
        swept = await storage.sweep_orphaned_run_principals(
            timedelta(minutes=5),
        )
        assert swept == 0
        assert await storage.get_principal(run_principal.id) is not None

    async def test_orphan_with_event_history_raises_fk_error(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """A kind=run orphan with events raises an integrity-constraint error.

        This scenario is structurally impossible in normal operation: a
        principal whose run was never created cannot have events. The
        test verifies the FK safety net: the DELETE matches the row
        (kind=run, no matching run, old enough), PG tries to remove it,
        the RESTRICT FK on events.principal_id fires, and the
        transaction rolls back -- the principal survives.

        The concrete exception class is PostgreSQL-version-dependent:
        ForeignKeyViolationError (23503) through PG 17, RestrictViolationError
        (23001) from PG 18. The sweep deliberately does not translate it (only
        delete_principal does), so the assertion is on their shared base.
        """
        import asyncpg as _asyncpg

        storage = PgPrincipalStorage(pg_pool)
        ref = uuid6.uuid7()
        minted = await storage.mint_principal(KIND_RUN, ref, None)
        # Attribute an event to this principal so the FK blocks deletion.
        await pg_pool.execute(
            "INSERT INTO events (event_type, principal_id)"
            " VALUES ($1, $2)",
            "test.event", minted.id,
        )
        # Backdate to be old enough.
        await pg_pool.execute(
            "UPDATE principals SET created_at = now() - interval '10 minutes'"
            " WHERE id = $1",
            minted.id,
        )
        with pytest.raises(_asyncpg.IntegrityConstraintViolationError):
            await storage.sweep_orphaned_run_principals(
                timedelta(minutes=5),
            )
        # The principal survives the failed sweep.
        assert await storage.get_principal(minted.id) is not None

    async def test_non_run_kind_is_not_swept(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """A kind=consumer orphan is NOT swept (kind=run scope only)."""
        storage = PgPrincipalStorage(pg_pool)
        ref = uuid6.uuid7()
        minted = await storage.mint_principal(KIND_CONSUMER, ref, "consumer")
        # Backdate.
        await pg_pool.execute(
            "UPDATE principals SET created_at = now() - interval '10 minutes'"
            " WHERE id = $1",
            minted.id,
        )
        swept = await storage.sweep_orphaned_run_principals(
            timedelta(minutes=5),
        )
        assert swept == 0
        assert await storage.get_principal(minted.id) is not None
