"""SSE stream endpoint: GET /events/{slug}/stream.

Hand-built catch-up pattern per the decision record:
1. Accept the SSE connection
2. Check for Last-Event-ID header (client's last-seen event ID)
3. If present: query replay(since_id=last_event_id, source=slug)
4. Subscribe to LISTEN/NOTIFY on EVENTS_CHANNEL
5. Send catch-up events first
6. Stream live events, deduplicating by event ID
7. Fetch-on-notify: NOTIFY payload lacks data, so fetch full event by ID

SSE event format: id: <event_id>\\nevent: <event_type>\\ndata: <json>\\n\\n
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastware import StreamResponse, TextResponse
from orxtra.auth import AuthenticationError
from orxtra.trace import EVENTS_CHANNEL, read_event, replay

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    import asyncpg
    from orxtra.auth import Authenticator
    from orxtra.protocols import DispatchBackend, EventBus, Source

log = logging.getLogger(__name__)


def _serialize_value(value: Any) -> Any:  # noqa: ANN401
    """Convert UUIDs and datetimes to strings for JSON serialization."""
    if isinstance(value, UUID):
        return str(value)
    if hasattr(value, "isoformat"):
        return value.isoformat()
    return value


def _serialize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Serialize an event dict for JSON output."""
    return {k: _serialize_value(v) for k, v in event.items()}


def _format_sse_event(event: dict[str, Any]) -> str:
    """Format an event dict as an SSE wire message.

    Format: id: <event_id>\\nevent: <event_type>\\ndata: <json>\\n\\n
    """
    serialized = _serialize_event(event)
    event_id = serialized.get("id", "")
    event_type = serialized.get("event_type", "message")
    data = json.dumps(serialized)
    return f"id: {event_id}\nevent: {event_type}\ndata: {data}\n\n"


async def stream_handler(
    request: Any,  # noqa: ANN401
    *,
    pool: asyncpg.Pool[Any],
    dispatch_backend: DispatchBackend,
    authenticator: Authenticator,
    event_bus: EventBus,
) -> StreamResponse | TextResponse:
    """GET /events/{slug}/stream -- SSE stream with catch-up."""
    slug: str = request.path_params.get("slug", "")

    # -- Source lookup --
    source: Source | None = await dispatch_backend.get_source_by_slug(slug)
    if source is None:
        return TextResponse(f"Source not found: {slug}", status=404)

    # -- Reject unauthenticated sources --
    credential_id = source.credential_id
    if credential_id is None:
        return TextResponse(
            "Source has no credential configured",
            status=403,
        )

    # -- Authenticate via Authorization header --
    source_config: dict[str, Any] = source.config or {}
    auth_header_name = source_config.get("auth_header", "Authorization")
    auth_value = request.header(auth_header_name)
    if auth_value is None:
        return TextResponse(
            f"Missing authentication header: {auth_header_name}",
            status=401,
        )
    presented = auth_value
    if presented.lower().startswith("bearer "):
        presented = presented[7:]

    try:
        await authenticator.verify_by_credential_id(credential_id, presented)
    except AuthenticationError:
        return TextResponse("Authentication failed", status=401)

    # -- Parse Last-Event-ID --
    last_event_id_raw = request.header("last-event-id")
    last_event_id: UUID | None = None
    if last_event_id_raw is not None:
        try:
            last_event_id = UUID(last_event_id_raw)
        except ValueError:
            return TextResponse(
                f"Invalid Last-Event-ID: {last_event_id_raw!r}",
                status=400,
            )

    log.info(
        "SSE stream connected: slug=%s last_event_id=%s",
        slug,
        last_event_id,
    )

    # -- Build the SSE generator with catch-up --
    generator = _sse_generator(
        pool=pool,
        event_bus=event_bus,
        slug=slug,
        last_event_id=last_event_id,
    )

    return StreamResponse(
        generator,
        content_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
    )


async def _fetch_live_event(
    notification: dict[str, Any],
    pool: asyncpg.Pool[Any],
    seen_ids: set[str],
) -> str | None:
    """Fetch a full event from DB after a NOTIFY, with deduplication.

    Returns the formatted SSE string, or None if the event should be
    skipped (already seen, missing, or invalid).
    """
    raw_event_id = notification.get("event_id")
    if raw_event_id is None:
        return None

    event_id_str = str(raw_event_id)
    if event_id_str in seen_ids:
        return None

    try:
        event_uuid = UUID(event_id_str)
    except ValueError:
        return None

    full_event = await read_event(pool, event_uuid)
    if full_event is None:
        return None

    seen_ids.add(event_id_str)
    return _format_sse_event(full_event)


async def _sse_generator(
    *,
    pool: asyncpg.Pool[Any],
    event_bus: EventBus,
    slug: str,
    last_event_id: UUID | None,
) -> AsyncGenerator[str, None]:
    """Async generator implementing the hand-built catch-up pattern.

    Order of operations (no-loss guarantee):
    1. Subscribe to LISTEN/NOTIFY first (before replay query).
    2. Replay historical events since last_event_id.
    3. Send catch-up events.
    4. Stream live events, deduplicating against already-sent IDs.
    """
    live_queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue()

    async def _on_notify(payload: str) -> None:
        try:
            parsed: dict[str, Any] = json.loads(payload)
        except (json.JSONDecodeError, ValueError):
            return
        if parsed.get("source") != slug:
            return
        await live_queue.put(parsed)

    # Step 1: Subscribe BEFORE replay to avoid gaps.
    await event_bus.subscribe(EVENTS_CHANNEL, _on_notify)

    try:
        # Step 2+3: Replay catch-up events and track IDs.
        seen_ids: set[str] = set()
        if last_event_id is not None:
            for event in await replay(pool, source=slug, since_id=last_event_id):
                seen_ids.add(str(event["id"]))
                yield _format_sse_event(event)

        # Step 4: Stream live events with deduplication.
        while True:
            try:
                notification = await asyncio.wait_for(
                    live_queue.get(), timeout=15.0,
                )
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue

            sse_msg = await _fetch_live_event(notification, pool, seen_ids)
            if sse_msg is not None:
                yield sse_msg

    except asyncio.CancelledError:
        return
