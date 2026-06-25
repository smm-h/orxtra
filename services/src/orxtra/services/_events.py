from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.trace import TraceWriter

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


async def fire_event(
    pool: asyncpg.Pool,
    run_id: UUID,
    event_name: str,
    payload: dict[str, Any] | None = None,
) -> UUID:
    writer = TraceWriter(pool)
    return await writer.write_event(run_id, event_name, payload or {})
