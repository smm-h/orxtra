"""AG-UI SSE endpoint using fastware's Broadcaster and Router."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ag_ui.core import (
    BaseEvent,
    EventType,
)
from ag_ui.encoder import EventEncoder
from fastware import Router, TextResponse
from fastware.sse import Broadcaster
from orxtra.agui._sinks import AGUIOverseerSink, AGUITransportSink
from orxtra.agui._translator import AGUITranslator
from orxtra.identity import resolve_caller_principal
from orxtra.protocols import TrustTier
from orxtra.services import get_run

if TYPE_CHECKING:
    import asyncpg
    from fastware import Request, StreamResponse
    from orxtra.protocols import AuthContext, PrincipalStorage

log = logging.getLogger(__name__)

# All AG-UI event type values that the broadcaster may emit.
_AG_UI_EVENT_TYPES: list[str] = [member.value for member in EventType]


def _create_broadcaster() -> Broadcaster:
    """Create a Broadcaster pre-registered with all AG-UI event types."""
    return Broadcaster(strict=False, heartbeat_interval=15.0)


async def _check_run_access(
    auth_context: AuthContext,
    run_id: str,
    *,
    pool: asyncpg.Pool | None,
    principal_storage: PrincipalStorage | None,
) -> TextResponse | None:
    """Authorize an authenticated caller to stream ``run_id``.

    Returns ``None`` when the caller may stream the run, or an error
    ``TextResponse`` (404/403) to return instead. The caller must already be
    authenticated -- the ``auth_context`` absence check (401) lives in the
    handler, before this is reached.

    Rules:

    - A SYSTEM-tier caller (the operator) may stream any run. This is a
      short-circuit: the operator needs no persisted principal and no run
      lookup.
    - Otherwise the caller's principal is resolved (a consumer without a
      backing principal is an integrity violation and raises, never a silent
      denial), the run's ``created_by`` is loaded, and access is granted only
      when it matches the caller's principal id.

    Status choice for an existing-but-unowned run is 403 (not 404),
    following the repo precedent in ``orxtra.incoming``: an unknown resource
    is 404 while a known resource the caller may not use is 403. A run id that
    is not a well-formed UUID, or that resolves to no run, is 404 -- both are
    "no such run" and are reported uniformly so the response never leaks
    whether a given id maps to a real run beyond that single 404-vs-403 line.
    The 403 body names no other principal.
    """
    if auth_context.trust_tier == TrustTier.SYSTEM:
        return None

    if pool is None or principal_storage is None:
        msg = (
            "AG-UI router misconfigured: streaming a run requires a database "
            "pool and a principal storage backend."
        )
        raise RuntimeError(msg)

    caller = await resolve_caller_principal(auth_context, principal_storage)

    try:
        run_uuid = UUID(run_id)
    except ValueError:
        return TextResponse("Run not found", status=404)

    report = await get_run(pool, run_uuid)
    if report is None:
        return TextResponse("Run not found", status=404)

    if report.created_by != caller.id:
        return TextResponse(
            "You are not authorized to stream this run", status=403,
        )

    return None


def create_agui_router(
    *,
    pool: asyncpg.Pool | None,
    principal_storage: PrincipalStorage | None,
    subscribe_run: Any = None,
) -> tuple[Router, Broadcaster]:
    """Create a Router with an SSE route for AG-UI event streaming.

    Returns (router, broadcaster) so the caller can wire the broadcaster
    into the active run's event delivery.

    ``pool`` and ``principal_storage`` back the per-run access check: the
    caller's principal is resolved against ``principal_storage`` and the run's
    owner is loaded via ``pool``. The compositor injects both from the
    dispatch context; they are declared explicitly (no service-locator lookup).

    ``subscribe_run`` is reserved for Phase 8 integration. When provided,
    it will be called with (run_id, transport_sink, overseer_sink) to
    register sinks with the scheduler for the active run.
    """
    broadcaster = _create_broadcaster()
    encoder = EventEncoder()

    router = Router()

    async def events_handler(request: Request) -> StreamResponse | TextResponse:
        """SSE endpoint at /events?run_id=...&thread_id=..."""
        # -- Access control (before any stream setup) --
        auth_context: AuthContext | None = request.state.get("auth_context")
        if auth_context is None:
            # Open mode (no auth wall) cannot stream runs, consistent with
            # dispatch enforcement. Configure an authenticator to enable it.
            return TextResponse(
                "Streaming runs requires authentication; this server has no "
                "authenticator configured.",
                status=401,
            )

        run_id = request.query("run_id", "")
        thread_id = request.query("thread_id", run_id)
        thinking = request.query("thinking", "silent")

        if not run_id:
            return TextResponse("run_id query parameter is required", status=400)

        denied = await _check_run_access(
            auth_context,
            run_id,
            pool=pool,
            principal_storage=principal_storage,
        )
        if denied is not None:
            return denied

        translator = AGUITranslator(
            thread_id=thread_id,
            run_id=run_id,
            thinking_visibility=thinking,
        )

        async def push_event(event: BaseEvent) -> None:
            encoded = encoder.encode(event)
            # Broadcaster.broadcast expects (event_type, data).
            # We use the encoded SSE string directly as data.
            broadcaster.broadcast("message", encoded)

        transport_sink = AGUITransportSink(translator, push_event)
        overseer_sink = AGUIOverseerSink(translator, push_event)

        if subscribe_run is not None:
            await subscribe_run(run_id, transport_sink, overseer_sink)

        log.info(
            "AG-UI SSE client connected for run_id=%s thread_id=%s",
            run_id,
            thread_id,
        )
        return await broadcaster.stream(request)

    router.add_route("GET", "/events", events_handler)

    return router, broadcaster
