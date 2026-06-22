from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


class RunLockError(Exception):
    pass


def _lock_key(run_id: UUID) -> int:
    return int(run_id.hex[:16], 16) & 0x7FFFFFFFFFFFFFFF


async def acquire_run_lock(pool: asyncpg.Pool, run_id: UUID) -> None:
    key = _lock_key(run_id)
    acquired: bool = await pool.fetchval(
        "SELECT pg_try_advisory_lock($1)", key
    )
    if not acquired:
        msg = f"run {run_id} is already locked by another process"
        raise RunLockError(msg)


async def release_run_lock(pool: asyncpg.Pool, run_id: UUID) -> None:
    key = _lock_key(run_id)
    await pool.execute("SELECT pg_advisory_unlock($1)", key)


async def update_heartbeat(pool: asyncpg.Pool, run_id: UUID) -> None:
    await pool.execute(
        "INSERT INTO run_heartbeats (run_id, last_heartbeat)"
        " VALUES ($1, now())"
        " ON CONFLICT (run_id) DO UPDATE SET last_heartbeat = now()",
        run_id,
    )


async def is_lock_stale(
    pool: asyncpg.Pool, run_id: UUID, threshold_seconds: float = 300.0
) -> bool:
    row = await pool.fetchrow(
        "SELECT EXTRACT(EPOCH FROM now() - last_heartbeat) > $2 AS is_stale"
        " FROM run_heartbeats WHERE run_id = $1",
        run_id,
        threshold_seconds,
    )
    if row is None:
        return True
    return bool(row["is_stale"])
