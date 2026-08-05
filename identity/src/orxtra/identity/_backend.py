from __future__ import annotations

from typing import TYPE_CHECKING

import asyncpg
from orxtra.identity._exceptions import PrincipalInUseError
from orxtra.protocols import Principal

if TYPE_CHECKING:
    from datetime import timedelta
    from uuid import UUID


_SELECT_COLUMNS = "id, kind, external_ref, display_name, created_at"


class PgPrincipalStorage:
    """asyncpg-backed principal storage implementing ``PrincipalStorage``.

    The pool is stored at construction and used internally -- callers never
    pass it per-call. Each method acquires a connection and opens a
    transaction, matching the auth backend's convention.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def mint_principal(
        self,
        kind: str,
        external_ref: UUID,
        display_name: str | None,
    ) -> Principal:
        """Idempotent upsert on ``(kind, external_ref)``.

        Inserts the principal if absent (``ON CONFLICT DO NOTHING``) then
        fetches the row in the same transaction, returning the persisted
        ``Principal`` whether it was just created or already existed. Never
        errors on duplicates, so callers may mint unconditionally on every
        actor appearance and converge to a single row.

        Crash-safe: an orphaned row left by a prior creation that crashed
        before returning is simply fetched and returned here.

        Does NOT validate ``kind``. Kind validation is owned by the service
        layer (via ``KindRegistry``); storage accepts any string, matching
        the schema comment on ``principals.kind``.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO principals (kind, external_ref, display_name)"
                " VALUES ($1, $2, $3)"
                " ON CONFLICT (kind, external_ref) DO NOTHING",
                kind,
                external_ref,
                display_name,
            )
            row = await conn.fetchrow(
                f"SELECT {_SELECT_COLUMNS} FROM principals"  # noqa: S608
                " WHERE kind = $1 AND external_ref = $2",
                kind,
                external_ref,
            )
        assert row is not None  # noqa: S101
        return _row_to_principal(row)

    async def get_principal(self, principal_id: UUID) -> Principal | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT {_SELECT_COLUMNS} FROM principals"  # noqa: S608
                " WHERE id = $1",
                principal_id,
            )
        if row is None:
            return None
        return _row_to_principal(row)

    async def get_principal_by_ref(
        self,
        kind: str,
        external_ref: UUID,
    ) -> Principal | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"SELECT {_SELECT_COLUMNS} FROM principals"  # noqa: S608
                " WHERE kind = $1 AND external_ref = $2",
                kind,
                external_ref,
            )
        if row is None:
            return None
        return _row_to_principal(row)

    async def list_principals(self, kind: str | None = None) -> list[Principal]:
        if kind is not None:
            query = (
                f"SELECT {_SELECT_COLUMNS} FROM principals"  # noqa: S608
                " WHERE kind = $1 ORDER BY created_at"
            )
            args: tuple[object, ...] = (kind,)
        else:
            query = (
                f"SELECT {_SELECT_COLUMNS} FROM principals"  # noqa: S608
                " ORDER BY created_at"
            )
            args = ()
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(query, *args)
        return [_row_to_principal(row) for row in rows]

    async def update_display_name(
        self,
        principal_id: UUID,
        display_name: str,
    ) -> None:
        """Set the display name of an existing principal.

        Hard error (``KeyError``) if the principal is absent -- this is not
        an upsert. ``KeyError`` matches the auth backend's convention for
        absent rows (``InMemoryAuthBackend.disable_consumer``).
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE principals SET display_name = $2"
                " WHERE id = $1 RETURNING id",
                principal_id,
                display_name,
            )
        if row is None:
            msg = f"Principal {principal_id} not found"
            raise KeyError(msg)

    async def delete_principal(self, principal_id: UUID) -> None:
        """Delete a principal.

        Raises ``PrincipalInUseError`` if the principal anchors durable
        history. The referencing RESTRICT foreign keys are ``events.principal_id``,
        ``runs.created_by``, ``sources.created_by``, ``inbox_items.resolved_by``,
        and ``consumers.principal_id``.

        Which PostgreSQL error a RESTRICT violation raises is version-dependent:
        through PG 17 it is ``foreign_key_violation`` (23503,
        ``ForeignKeyViolationError``); PG 18 raises ``restrict_violation``
        (23001, ``RestrictViolationError``), which is a sibling class and NOT a
        subclass. Both are caught and translated into the domain error here.

        The one CASCADE referent, ``subscriptions.principal_id``, does not block:
        deleting an owning principal takes its subscriptions (operational state)
        with it. A principal that is neither referenced by history nor owns any
        subscription deletes cleanly.
        """
        try:
            async with self._pool.acquire() as conn, conn.transaction():
                await conn.execute(
                    "DELETE FROM principals WHERE id = $1",
                    principal_id,
                )
        except (
            asyncpg.ForeignKeyViolationError,
            asyncpg.RestrictViolationError,
        ) as exc:
            raise PrincipalInUseError(principal_id) from exc

    async def sweep_orphaned_run_principals(
        self, older_than: timedelta,
    ) -> int:
        """Delete kind=run principals with no matching ``runs`` row.

        Orphaned run principals are created by a mint-first that crashed
        before ``create_run``. They are harmless (nothing references them)
        and swept by recovery (age-guarded). The ``older_than`` guard
        closes the race with a concurrent ``start_run``: a principal
        minted moments ago may not yet have its runs row.

        The RESTRICT FKs on events/subscriptions/etc. independently
        prevent deleting any referenced principal; the WHERE clause is a
        performance optimization (avoids FK-violation churn), not the
        safety mechanism.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            result: str = await conn.execute(
                "DELETE FROM principals"
                " WHERE kind = 'run'"
                "   AND NOT EXISTS ("
                "       SELECT 1 FROM runs WHERE runs.id = principals.external_ref"
                "   )"
                "   AND created_at < now() - $1::interval",
                older_than,
            )
        # asyncpg execute returns "DELETE N"
        return int(result.rsplit(maxsplit=1)[-1])


def _row_to_principal(row: asyncpg.Record) -> Principal:
    return Principal(
        id=row["id"],
        kind=row["kind"],
        external_ref=row["external_ref"],
        display_name=row["display_name"],
        created_at=row["created_at"],
    )
