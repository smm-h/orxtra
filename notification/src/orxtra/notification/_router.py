"""Notification SSE route: GET /stream.

Mounted by the api compositor behind the auth wall. The handler
resolves the caller's principal, then streams their notifications
via the catch-up + live SSE generator.

SYSTEM-tier callers may pass ``?principal_id=<uuid>`` to stream
another principal's notifications. Non-SYSTEM callers that pass a
different principal_id receive a 403.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from fastware import Router, StreamResponse, TextResponse
from orxtra.identity import resolve_caller_principal
from orxtra.notification._stream import notification_sse_generator
from orxtra.protocols import TrustTier

if TYPE_CHECKING:
    from orxtra.protocols import EventBus, NotificationPort, PrincipalStorage

log = logging.getLogger(__name__)


def create_notification_router(
    *,
    notification_port: NotificationPort,
    event_bus: EventBus,
    principal_storage: PrincipalStorage,
) -> Router:
    """Create a Router with the notification SSE stream route.

    Args:
        notification_port: Backend for notification delivery queries.
        event_bus: EventBus for LISTEN/NOTIFY subscription.
        principal_storage: Resolves the caller's AuthContext to a
            persisted Principal.

    Returns:
        A fastware Router with ``GET /stream`` registered.
    """
    router = Router()

    async def stream_handler(
        request: Any,
    ) -> StreamResponse | TextResponse:
        """GET /stream -- SSE stream of principal notifications."""
        from orxtra.protocols import AuthContext

        auth_context: AuthContext | None = request.state.get("auth_context")
        if auth_context is None:
            return TextResponse(
                "Streaming notifications requires authentication; this "
                "server has no authenticator configured.",
                status=401,
            )

        # Resolve the caller's persisted principal.
        caller = await resolve_caller_principal(
            auth_context, principal_storage,
        )

        # Determine which principal's notifications to stream.
        target_principal_id = caller.id
        requested_id_raw: str | None = request.query("principal_id")
        if requested_id_raw is not None:
            try:
                requested_id = UUID(requested_id_raw)
            except ValueError:
                return TextResponse(
                    f"Invalid principal_id: {requested_id_raw!r}",
                    status=400,
                )

            if requested_id != caller.id:
                if auth_context.trust_tier != TrustTier.SYSTEM:
                    return TextResponse(
                        "Only SYSTEM-tier callers may stream another "
                        "principal's notifications",
                        status=403,
                    )
                target_principal_id = requested_id

        # Parse Last-Event-ID header for catch-up.
        last_event_id: str | None = request.header("last-event-id")

        log.info(
            "Notification SSE stream connected: "
            "caller=%s target=%s last_event_id=%s",
            caller.id,
            target_principal_id,
            last_event_id,
        )

        generator = notification_sse_generator(
            notification_port=notification_port,
            event_bus=event_bus,
            principal_id=target_principal_id,
            last_event_id=last_event_id,
        )

        return StreamResponse(
            generator,
            content_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "Connection": "keep-alive"},
        )

    router.add_route("GET", "/stream", stream_handler)

    return router
