from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

import uuid6
from orxtra.trace._lock import lock_key

if TYPE_CHECKING:
    import asyncpg


# The system principal's external_ref is the all-zeros UUID sentinel (mirrors
# protocols.SYSTEM_PRINCIPAL_EXTERNAL_REF). trace is a zero-intra-workspace-dep
# foundation module, so recovery resolves the system principal id with a raw
# SELECT rather than importing identity's storage or the protocols constant.
_SYSTEM_PRINCIPAL_EXTERNAL_REF = UUID(int=0)


async def _resolve_system_principal_id(
    conn: asyncpg.pool.PoolConnectionProxy[asyncpg.Record],
) -> UUID:
    """Resolve the singleton system principal id, or hard-error if unseeded.

    Crash-recovery events are emitted by the machinery itself, not by any run
    or caller, so they attribute to the SYSTEM principal. There is no silent
    NULL or inline-subquery path: if the system principal row is absent the
    database was never seeded, which is a hard error (matches the wording used
    by ``resolve_caller_principal`` in the services/identity layer).
    """
    principal_id: UUID | None = await conn.fetchval(
        "SELECT id FROM principals"
        " WHERE kind = 'system' AND external_ref = $1",
        _SYSTEM_PRINCIPAL_EXTERNAL_REF,
    )
    if principal_id is None:
        msg = (
            "System principal not seeded -- run 'orxtra db init' to seed "
            "the singleton system principal before crash recovery."
        )
        raise RuntimeError(msg)
    return principal_id


async def reclaim_interrupted(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT id, run_id FROM tasks"
            " WHERE status IN ('active', 'prechecking', 'postchecking')"
        )
        if not rows:
            return 0
        system_principal_id = await _resolve_system_principal_id(conn)
        task_ids = [row["id"] for row in rows]
        await conn.execute(
            "UPDATE tasks SET status = 'cancelled'"
            " WHERE id = ANY($1::uuid[])",
            task_ids,
        )
        for row in rows:
            await conn.execute(
                "INSERT INTO events"
                " (id, run_id, task_id, principal_id, event_type, data)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                uuid6.uuid7(),
                row["run_id"],
                row["id"],
                system_principal_id,
                "crash_recovery",
                json.dumps({"action": "reclaim_interrupted"}),
            )
    return len(rows)


async def reevaluate_blocked(pool: asyncpg.Pool) -> list[UUID]:
    rows = await pool.fetch(
        "SELECT t.id FROM tasks t"
        " WHERE t.status = 'created'"
        " AND (t.parent_task_id IS NULL OR EXISTS ("
        "     SELECT 1 FROM tasks p"
        "     WHERE p.id = t.parent_task_id AND p.status = 'completed'"
        " ))"
    )
    return [row["id"] for row in rows]


async def clean_orphaned(pool: asyncpg.Pool) -> int:
    rows = await pool.fetch(
        "SELECT id FROM runs WHERE status IN ('running', 'paused')"
    )
    if not rows:
        return 0
    # Resolve the system principal up front, BEFORE acquiring any advisory
    # lock. pg_try_advisory_lock is session-scoped, so a transaction rollback
    # would NOT release it -- resolving here means an unseeded database fails
    # cleanly with no leaked lock.
    async with pool.acquire() as conn:
        system_principal_id = await _resolve_system_principal_id(conn)
    cleaned = 0
    for row in rows:
        run_id: UUID = row["id"]
        key = lock_key(run_id)
        async with pool.acquire() as conn:
            acquired: bool = await conn.fetchval(
                "SELECT pg_try_advisory_lock($1)", key
            )
            if not acquired:
                continue
            # Lock acquired means the original holder crashed.
            # Transition run to failed, insert event, release the lock.
            async with conn.transaction():
                await conn.execute(
                    "UPDATE runs SET status = 'failed',"
                    " finished_at = now()"
                    " WHERE id = $1",
                    run_id,
                )
                await conn.execute(
                    "INSERT INTO events"
                    " (id, run_id, task_id, principal_id, event_type, data)"
                    " VALUES ($1, $2, $3, $4, $5, $6)",
                    uuid6.uuid7(),
                    run_id,
                    None,
                    system_principal_id,
                    "crash_recovery",
                    json.dumps({"action": "clean_orphaned"}),
                )
            await conn.execute("SELECT pg_advisory_unlock($1)", key)
            cleaned += 1
    return cleaned
