"""SSE stream for principal notifications: catch-up + live.

Replicates the incoming/_stream.py pattern against
``notification_deliveries``:
1. Subscribe to LISTEN/NOTIFY on ``orxtra_notifications`` (before replay).
2. Replay unacknowledged deliveries for the principal (optionally after
   a cursor).
3. Stream live deliveries, deduplicating the overlap window.
4. Heartbeat every 15 s to keep the connection alive.

SSE event format: id: <delivery_id>\nevent: notification\ndata: <json>\n\n
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from orxtra.protocols import EventBus, NotificationDelivery, NotificationPort

log = logging.getLogger(__name__)

# PG NOTIFY channel name -- must match the trigger in schema/notification.toml.
NOTIFICATIONS_CHANNEL: str = "orxtra_notifications"


def _serialize_value(value: Any) -> Any:
    """Convert UUIDs and datetimes to strings for JSON serialization."""
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize_delivery(delivery: NotificationDelivery) -> dict[str, Any]:
    """Serialize a NotificationDelivery to a JSON-safe dict."""
    return {
        "id": str(delivery.id),
        "target_principal_id": str(delivery.target_principal_id),
        "source_ref": delivery.source_ref,
        "payload": delivery.payload,
        "created_at": delivery.created_at.isoformat(),
        "acknowledged_at": (
            delivery.acknowledged_at.isoformat()
            if delivery.acknowledged_at is not None
            else None
        ),
    }


def _format_sse_event(delivery: NotificationDelivery) -> str:
    """Format a delivery as an SSE wire message.

    Format: id: <delivery_id>\nevent: notification\ndata: <json>\n\n
    """
    serialized = _serialize_delivery(delivery)
    data = json.dumps(serialized)
    return f"id: {delivery.id}\nevent: notification\ndata: {data}\n\n"


async def _fetch_live_delivery(
    notification: dict[str, Any],
    notification_port: NotificationPort,
    seen_ids: set[str],
) -> str | None:
    """Fetch a full delivery from DB after a NOTIFY, with deduplication.

    Returns the formatted SSE string, or None if the delivery should be
    skipped (already seen, missing, or not for this principal).
    """
    raw_id = notification.get("notification_id")
    if raw_id is None:
        return None

    delivery_id_str = str(raw_id)
    if delivery_id_str in seen_ids:
        return None

    try:
        UUID(delivery_id_str)
    except ValueError:
        return None

    # The notification_port.list_for_principal returns all unacked for a
    # principal. For a single delivery lookup, we use cursor = id - 1
    # equivalent, but the NotificationPort API does not expose get-by-id.
    # Instead, the caller pre-filters by principal and we fetch via
    # list_for_principal with the target_principal_id from the NOTIFY payload.
    target_str = notification.get("target_principal_id")
    if target_str is None:
        return None

    try:
        target_uuid = UUID(target_str)
    except ValueError:
        return None

    # Fetch the specific delivery via cursor pagination: everything after
    # cursor=(delivery_id - 1) limited to 1 would work but UUIDs don't
    # subtract. Instead, list recent deliveries and find ours.
    deliveries = await notification_port.list_for_principal(
        target_uuid,
        unacknowledged_only=False,
        limit=50,
    )
    for d in deliveries:
        if str(d.id) == delivery_id_str:
            seen_ids.add(delivery_id_str)
            return _format_sse_event(d)

    return None


async def notification_sse_generator(
    *,
    notification_port: NotificationPort,
    event_bus: EventBus,
    principal_id: UUID,
    last_event_id: str | None,
) -> AsyncGenerator[str, None]:
    """Async generator implementing the catch-up + live SSE pattern.

    Order of operations (no-loss guarantee):
    1. Subscribe to LISTEN/NOTIFY first (before replay query).
    2. Replay unacknowledged deliveries for the principal.
    3. Stream live deliveries, deduplicating against already-sent IDs.
    4. Heartbeat every 15 s.
    """
    live_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
    principal_str = str(principal_id)

    async def _on_notify(payload: str) -> None:
        try:
            parsed: dict[str, Any] = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return
        # Client-side filtering: only enqueue if the delivery targets
        # this principal.
        if parsed.get("target_principal_id") != principal_str:
            return
        await live_queue.put(parsed)

    # Step 1: Subscribe BEFORE replay to avoid gaps.
    await event_bus.subscribe(NOTIFICATIONS_CHANNEL, _on_notify)

    try:
        # Step 2: Replay catch-up deliveries and track IDs.
        seen_ids: set[str] = set()

        cursor: UUID | None = None
        if last_event_id is not None:
            with contextlib.suppress(ValueError):
                cursor = UUID(last_event_id)

        replay_deliveries = await notification_port.list_for_principal(
            principal_id,
            unacknowledged_only=True,
            cursor=cursor,
        )
        for delivery in replay_deliveries:
            seen_ids.add(str(delivery.id))
            yield _format_sse_event(delivery)

        # Step 3: Stream live deliveries with deduplication.
        get_task: asyncio.Task[dict[str, Any]] | None = None
        try:
            while True:
                get_task = asyncio.ensure_future(live_queue.get())
                try:
                    notification = await asyncio.wait_for(
                        asyncio.shield(get_task), timeout=15.0,
                    )
                except TimeoutError:
                    get_task.cancel()
                    with contextlib.suppress(asyncio.CancelledError):
                        await get_task
                    get_task = None
                    yield ": heartbeat\n\n"
                    continue

                get_task = None
                sse_msg = await _fetch_live_delivery(
                    notification, notification_port, seen_ids,
                )
                if sse_msg is not None:
                    yield sse_msg
        finally:
            if get_task is not None and not get_task.done():
                get_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await get_task

    except asyncio.CancelledError:
        return
    finally:
        await event_bus.unsubscribe(NOTIFICATIONS_CHANNEL, _on_notify)
