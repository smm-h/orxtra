from __future__ import annotations

import json
from typing import TYPE_CHECKING

import uuid6
from orxtra.trace._lock import lock_key

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


async def reclaim_interrupted(pool: asyncpg.Pool) -> int:
    async with pool.acquire() as conn, conn.transaction():
        rows = await conn.fetch(
            "SELECT id, run_id FROM tasks"
            " WHERE status IN ('active', 'prechecking', 'postchecking')"
        )
        if not rows:
            return 0
        task_ids = [row["id"] for row in rows]
        await conn.execute(
            "UPDATE tasks SET status = 'cancelled'"
            " WHERE id = ANY($1::uuid[])",
            task_ids,
        )
        for row in rows:
            await conn.execute(
                "INSERT INTO events (id, run_id, task_id, event_type, data)"
                " VALUES ($1, $2, $3, $4, $5)",
                uuid6.uuid7(),
                row["run_id"],
                row["id"],
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
                    "INSERT INTO events (id, run_id, task_id, event_type, data)"
                    " VALUES ($1, $2, $3, $4, $5)",
                    uuid6.uuid7(),
                    run_id,
                    None,
                    "crash_recovery",
                    json.dumps({"action": "clean_orphaned"}),
                )
            await conn.execute("SELECT pg_advisory_unlock($1)", key)
            cleaned += 1
    return cleaned
