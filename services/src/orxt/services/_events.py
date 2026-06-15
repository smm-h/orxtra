from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg  # type: ignore[import-untyped]


async def fire_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    event_name: str,
    payload: dict[str, Any] | None = None,
) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT id FROM runs WHERE id = $1", run_id
        )
        if row is None:
            msg = f"run {run_id} not found"
            raise ValueError(msg)
        notification = json.dumps({
            "run_id": str(run_id),
            "event": event_name,
            "payload": payload,
        })
        await conn.execute(
            "SELECT pg_notify($1, $2)", "orxt_events", notification
        )
