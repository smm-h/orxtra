"""asyncpg-backed notification backend implementing ``NotificationPort``.

The pool is stored at construction and used internally -- callers never
pass it per-call. Each method acquires a connection and opens a
transaction, matching the identity/_backend.py convention.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import asyncpg
from orxtra.protocols import NotificationDelivery

if TYPE_CHECKING:
    from uuid import UUID


_SELECT_COLUMNS = (
    "id, target_principal_id, source_ref, payload,"
    " created_at, acknowledged_at"
)


class PgNotificationBackend:
    """asyncpg-backed notification delivery implementing ``NotificationPort``."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def create_delivery(
        self,
        target_principal_id: UUID,
        source_ref: str,
        payload: dict[str, Any],
    ) -> UUID:
        """INSERT into notification_deliveries, return the id.

        The PG NOTIFY trigger fires automatically on INSERT.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "INSERT INTO notification_deliveries"
                " (target_principal_id, source_ref, payload)"
                " VALUES ($1, $2, $3::jsonb)"
                " RETURNING id",
                target_principal_id,
                source_ref,
                json.dumps(payload),
            )
        assert row is not None  # noqa: S101
        result: UUID = row["id"]
        return result

    async def list_for_principal(
        self,
        principal_id: UUID,
        *,
        unacknowledged_only: bool = True,
        cursor: UUID | None = None,
        limit: int = 50,
    ) -> list[NotificationDelivery]:
        """SELECT deliveries for a principal with optional filters."""
        clauses = ["target_principal_id = $1"]
        args: list[object] = [principal_id]
        idx = 2

        if unacknowledged_only:
            clauses.append("acknowledged_at IS NULL")

        if cursor is not None:
            clauses.append(f"id > ${idx}")
            args.append(cursor)
            idx += 1

        where = " AND ".join(clauses)
        query = (
            f"SELECT {_SELECT_COLUMNS}"  # noqa: S608
            f" FROM notification_deliveries"
            f" WHERE {where}"
            f" ORDER BY created_at ASC"
            f" LIMIT ${idx}"
        )
        args.append(limit)

        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(query, *args)

        return [_row_to_delivery(row) for row in rows]

    async def acknowledge(self, delivery_id: UUID) -> None:
        """UPDATE SET acknowledged_at = now() WHERE id AND unacked.

        0-row update is a hard error (KeyError), matching identity's
        convention for absent-row errors.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "UPDATE notification_deliveries"
                " SET acknowledged_at = now()"
                " WHERE id = $1 AND acknowledged_at IS NULL"
                " RETURNING id",
                delivery_id,
            )
        if row is None:
            msg = (
                f"Notification delivery {delivery_id} not found or"
                " already acknowledged"
            )
            raise KeyError(msg)


def _row_to_delivery(row: asyncpg.Record) -> NotificationDelivery:
    payload_raw = row["payload"]
    payload = json.loads(payload_raw) if isinstance(payload_raw, str) else payload_raw
    return NotificationDelivery(
        id=row["id"],
        target_principal_id=row["target_principal_id"],
        source_ref=row["source_ref"],
        payload=payload,
        created_at=row["created_at"],
        acknowledged_at=row["acknowledged_at"],
    )
