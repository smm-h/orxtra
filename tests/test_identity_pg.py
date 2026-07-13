"""PG round-trip tests for PgPrincipalStorage.

Exercises PgPrincipalStorage against a real PostgreSQL database via
testcontainers. Skips gracefully when docker is unavailable.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest
from orxtra.identity import PgPrincipalStorage
from orxtra.protocols import KIND_CONSUMER, KIND_RUN, KIND_SYSTEM

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
