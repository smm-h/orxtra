"""Webhook receiver, replay endpoint, and SSE stream on a fastware Router.

POST /events/{slug} -- webhook receiver (slug lookup, auth, mapping, 202).
GET /events/{slug}/replay -- cursor-based event replay.
GET /events/{slug}/stream -- SSE stream with hand-built catch-up.
"""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, cast

from fastware import JSONResponse, Router, TextResponse
from orxtra.auth import AuthenticationError
from orxtra.incoming._replay import replay_handler
from orxtra.incoming._stream import stream_handler
from orxtra.services import fire_event

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxtra.auth import Authenticator
    from orxtra.protocols import DispatchBackend, EventBus, Source

log = logging.getLogger(__name__)

# Default maximum request body size (1 MiB).
DEFAULT_MAX_BODY_BYTES = 1_048_576


def create_incoming_router(
    *,
    pool: asyncpg.Pool[Any],
    dispatch_backend: DispatchBackend,
    authenticator: Authenticator,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
    event_bus: EventBus | None = None,
) -> Router:
    """Create a Router with webhook, replay, and SSE stream routes.

    Args:
        pool: asyncpg pool for fire_event and trace queries.
        dispatch_backend: For source lookup by slug.
        authenticator: For credential verification (verify_by_credential_id).
        max_body_bytes: Maximum allowed request body size in bytes.
        event_bus: EventBus for SSE streaming (LISTEN/NOTIFY). When None,
            the SSE stream endpoint is not registered.

    Returns:
        A fastware Router with the incoming routes registered.
    """
    router = Router()

    async def webhook_handler(request: Any) -> JSONResponse | TextResponse:
        """POST /events/{slug} -- receive an external webhook event."""
        slug: str = request.path_params.get("slug", "")

        # -- Body-size cap --
        raw_body: bytes = request.body
        if len(raw_body) > max_body_bytes:
            return TextResponse(
                "Request body too large",
                status=413,
            )

        # -- Source lookup --
        source: Source | None = await dispatch_backend.get_source_by_slug(slug)
        if source is None:
            return TextResponse(f"Source not found: {slug}", status=404)

        # -- Reject unauthenticated sources --
        credential_id: UUID | None = source.credential_id
        if credential_id is None:
            return TextResponse(
                "Source has no credential configured",
                status=403,
            )

        # -- Build presented_credential for the authenticator --
        source_config: dict[str, Any] = source.config or {}
        try:
            presented = _build_presented_credential(
                request, raw_body, source_config,
            )
        except _ExtractionError as exc:
            return TextResponse(str(exc), status=401)

        # -- Verify credential --
        try:
            # Phase 4 will attribute the fired event to this AuthContext;
            # for now we capture (not discard) the verification result and
            # hold it in hand at the fire_event call site below.
            auth_context = await authenticator.verify_by_credential_id(
                credential_id, presented,
            )
        except AuthenticationError:
            return TextResponse("Authentication failed", status=401)

        # -- Extract event_type from the payload --
        try:
            event_type = _extract_event_type(request, raw_body, source_config)
        except _ExtractionError as exc:
            return TextResponse(str(exc), status=400)

        # -- Parse body as JSON --
        try:
            data: dict[str, Any] = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError):
            return TextResponse("Invalid JSON body", status=400)

        # -- Extract idempotency key --
        idempotency_key = _extract_idempotency_key(request, source_config)

        # -- Fire event --
        event_id, inserted = await fire_event(
            pool,
            None,  # run_id -- external events are not tied to a run
            event_type,
            data,
            source=slug,
            idempotency_key=idempotency_key,
        )

        log.info(
            "Webhook received: slug=%s event_type=%s event_id=%s "
            "inserted=%s consumer_id=%s",
            slug,
            event_type,
            event_id,
            inserted,
            auth_context.consumer_id,
        )

        return JSONResponse(
            {"event_id": str(event_id), "inserted": inserted},
            status=202,
        )

    router.add_route("POST", "/events/{slug}", webhook_handler)

    # -- Replay endpoint --

    async def _replay_handler(request: Any) -> JSONResponse | TextResponse:
        return await replay_handler(
            request,
            pool=pool,
            dispatch_backend=dispatch_backend,
            authenticator=authenticator,
        )

    router.add_route("GET", "/events/{slug}/replay", _replay_handler)

    # -- SSE stream endpoint (requires event_bus) --

    if event_bus is not None:
        async def _stream_handler(
            request: Any,
        ) -> Any:
            return await stream_handler(
                request,
                pool=pool,
                dispatch_backend=dispatch_backend,
                authenticator=authenticator,
                event_bus=event_bus,
            )

        router.add_route("GET", "/events/{slug}/stream", _stream_handler)

    return router


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


class _ExtractionError(Exception):
    """Raised when a required value cannot be extracted from the request."""


def _build_presented_credential(
    request: Any,
    raw_body: bytes,
    config: dict[str, Any],
) -> str:
    """Build the presented_credential string for the authenticator.

    For HMAC sources: the config must specify ``signature_header``.
    The presented credential is ``identifier:signature:message`` where
    identifier is a placeholder (the credential is looked up by ID,
    not hash), signature is from the header, and message is the raw body.

    For bearer/api_key sources: extracts the token from the Authorization
    header (or a custom header specified in ``auth_header``).
    """
    signature_header = config.get("signature_header")
    if signature_header is not None:
        # HMAC verification path.
        signature = request.header(signature_header)
        if signature is None:
            msg = f"Missing signature header: {signature_header}"
            raise _ExtractionError(msg)

        # Strip common prefixes (e.g., "sha256=" from GitHub).
        if "=" in signature and not signature.startswith("sha"):
            # Only strip if it looks like "algo=hex" and isn't already
            # the raw hex. GitHub sends "sha256=abc123..." -- strip prefix.
            pass
        if signature.startswith(("sha256=", "sha1=", "sha512=")):
            signature = signature.split("=", maxsplit=1)[1]

        # The HmacCredentialVerifier expects "identifier:signature:message".
        # The identifier is unused when verifying by credential ID, but the
        # format is required by the verifier's parser. Use "_" as a
        # placeholder.
        body_str = raw_body.decode("utf-8", errors="replace")
        return f"_:{signature}:{body_str}"

    # Bearer/api_key path: extract from Authorization header.
    auth_header_name = config.get("auth_header", "Authorization")
    auth_value = request.header(auth_header_name)
    if auth_value is None:
        msg = f"Missing authentication header: {auth_header_name}"
        raise _ExtractionError(msg)

    # Strip "Bearer " prefix if present.
    if auth_value.lower().startswith("bearer "):
        auth_value = auth_value[7:]

    return cast("str", auth_value)


def _extract_event_type(
    request: Any,
    raw_body: bytes,
    config: dict[str, Any],
) -> str:
    """Extract the event type from the request per source config.

    Config fields:
      - ``event_type_source``: "header", "json_field", or "constant"
      - ``event_type_field``: the header name, JSON field path, or
        constant value
    """
    source_type = config.get("event_type_source")
    field = config.get("event_type_field")

    if source_type is None or field is None:
        msg = (
            "Source config missing event_type_source or event_type_field; "
            "cannot determine event type"
        )
        raise _ExtractionError(msg)

    if source_type == "header":
        value = request.header(field)
        if value is None:
            msg = f"Missing event type header: {field}"
            raise _ExtractionError(msg)
        return cast("str", value)

    if source_type == "json_field":
        try:
            data = json.loads(raw_body)
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            msg = "Cannot parse body as JSON for event_type extraction"
            raise _ExtractionError(msg) from exc
        # Support dot-path for nested fields (e.g., "action.type").
        parts = field.split(".")
        current: Any = data
        for part in parts:
            if not isinstance(current, dict) or part not in current:
                msg = f"Event type field not found in body: {field}"
                raise _ExtractionError(msg)
            current = current[part]
        if not isinstance(current, str):
            msg = f"Event type field is not a string: {field}"
            raise _ExtractionError(msg)
        return current

    if source_type == "constant":
        return cast("str", field)

    msg = f"Unknown event_type_source: {source_type!r}"
    raise _ExtractionError(msg)


def _extract_idempotency_key(
    request: Any,
    config: dict[str, Any],
) -> str | None:
    """Extract the idempotency key from a configurable header."""
    header_name = config.get("idempotency_header")
    if header_name is None:
        return None
    return cast("str | None", request.header(header_name))
