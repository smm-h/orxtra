"""Replay endpoint: GET /events/{slug}/replay.

Returns historical events for a source as a JSON array, with cursor-based
pagination via UUIDv7 event IDs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastware import JSONResponse, TextResponse
from orxtra.auth import AuthenticationError
from orxtra.trace import replay

if TYPE_CHECKING:
    import asyncpg
    from orxtra.auth import Authenticator
    from orxtra.protocols import DispatchBackend, Source

log = logging.getLogger(__name__)

# Pagination limits.
DEFAULT_REPLAY_LIMIT = 100
MAX_REPLAY_LIMIT = 1000


def _serialize_event(event: dict[str, Any]) -> dict[str, Any]:
    """Serialize an event dict for JSON output.

    UUIDs and datetimes need string conversion.
    """
    result: dict[str, Any] = {}
    for key, value in event.items():
        if isinstance(value, UUID):
            result[key] = str(value)
        elif hasattr(value, "isoformat"):
            result[key] = value.isoformat()
        else:
            result[key] = value
    return result


async def replay_handler(
    request: Any,
    *,
    pool: asyncpg.Pool[Any],
    dispatch_backend: DispatchBackend,
    authenticator: Authenticator,
) -> JSONResponse | TextResponse:
    """GET /events/{slug}/replay -- replay historical events for a source."""
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
    # Strip "Bearer " prefix if present.
    presented = auth_value
    if presented.lower().startswith("bearer "):
        presented = presented[7:]

    try:
        # Phase 4 will attribute replayed access to this AuthContext; for
        # now we capture (not discard) the verification result and hold it
        # in hand at the replay call site below.
        auth_context = await authenticator.verify_by_credential_id(
            credential_id, presented,
        )
    except AuthenticationError:
        return TextResponse("Authentication failed", status=401)

    # -- Parse query params --
    since_raw = request.query("since")
    since_id: UUID | None = None
    if since_raw is not None:
        try:
            since_id = UUID(since_raw)
        except ValueError:
            return TextResponse(
                f"Invalid 'since' parameter: must be a valid UUID, got {since_raw!r}",
                status=400,
            )

    limit = request.query("limit", type_=int, ge=1, le=MAX_REPLAY_LIMIT)
    if limit is None:
        limit = DEFAULT_REPLAY_LIMIT

    # -- Query events --
    events = await replay(
        pool,
        source=slug,
        since_id=since_id,
        limit=limit,
    )

    serialized = [_serialize_event(e) for e in events]

    log.info(
        "Replay: slug=%s since=%s limit=%d returned=%d consumer_id=%s",
        slug,
        since_id,
        limit,
        len(serialized),
        auth_context.consumer_id,
    )

    return JSONResponse(serialized)
