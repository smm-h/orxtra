from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.trace import TraceWriter


async def fire_event(
    writer: TraceWriter,
    run_id: UUID,
    event_name: str,
    payload: dict[str, Any] | None = None,
) -> UUID:
    return await writer.write_event(run_id, event_name, payload or {})
