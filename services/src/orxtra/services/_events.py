from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING, Any

from orxtra.protocols import run_sync
from orxtra.trace import EVENTS_CHANNEL, TraceWriter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from uuid import UUID

    import asyncpg

    from orxtra.protocols import EventBus


async def fire_event(
    pool: asyncpg.Pool,
    run_id: UUID | None,
    event_name: str,
    payload: dict[str, Any] | None = None,
    source: str = "internal",
    idempotency_key: str | None = None,
) -> tuple[UUID, bool]:
    """Fire an event into the trace store.

    Returns ``(event_id, inserted)``.  When *idempotency_key* is provided
    and the key already exists, *inserted* is ``False`` and the event is
    silently deduplicated.
    """
    writer = TraceWriter(pool)
    return await writer.write_event(
        run_id, event_name, payload or {},
        source=source, idempotency_key=idempotency_key,
    )


def fire_blocking(
    pool: asyncpg.Pool,
    run_id: UUID | None,
    event_name: str,
    payload: dict[str, Any] | None = None,
    source: str = "internal",
    idempotency_key: str | None = None,
) -> tuple[UUID, bool]:
    """Synchronous wrapper around fire_event for non-async contexts."""
    return run_sync(
        fire_event(pool, run_id, event_name, payload, source, idempotency_key),
    )


async def event_stream(
    bus: EventBus,
    *,
    channel: str = EVENTS_CHANNEL,
    run_id: UUID | None = None,
    event_types: list[str] | None = None,
) -> AsyncIterator[dict[str, Any]]:
    """Async generator that yields parsed events from an EventBus subscription.

    Subscribes to the given channel and yields each event as a parsed dict.
    Optional filters narrow the stream to a specific run_id or event types.
    """
    queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _on_event(payload: str) -> None:
        parsed: dict[str, Any] = json.loads(payload)
        if run_id is not None and parsed.get("run_id") != str(run_id):
            return
        if event_types is not None and parsed.get("event_type") not in event_types:
            return
        await queue.put(parsed)

    await bus.subscribe(channel, _on_event)

    try:
        while True:
            event = await queue.get()
            yield event
    finally:
        await bus.unsubscribe(channel, _on_event)
