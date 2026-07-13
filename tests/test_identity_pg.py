"""PG round-trip tests for PgPrincipalStorage.

Exercises PgPrincipalStorage against a real PostgreSQL database via
testcontainers. Skips gracefully when docker is unavailable.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
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
    sources.created_by, consumers.principal_id -- all RESTRICT) is undeletable.
    A principal that only owns operational state (subscriptions -- CASCADE) is
    deletable, taking that state with it. A never-referenced principal deletes
    cleanly.
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

    async def test_never_acted_principal_deletes_cleanly(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """(f) A principal referenced by nothing deletes cleanly."""
        storage = PgPrincipalStorage(pg_pool)
        idle = await storage.mint_principal(KIND_CONSUMER, uuid6.uuid7(), "idle")
        await storage.delete_principal(idle.id)
        assert await storage.get_principal(idle.id) is None
