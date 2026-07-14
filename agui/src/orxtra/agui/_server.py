"""AG-UI SSE endpoint using fastware's Broadcaster and Router."""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING, Any
from uuid import UUID

from ag_ui.core import (
    BaseEvent,
    EventType,
)
from ag_ui.encoder import EventEncoder
from fastware import Router, StreamResponse, TextResponse
from orxtra.agui._registry import _BroadcasterRegistry
from orxtra.agui._sinks import AGUIOverseerSink, AGUITransportSink
from orxtra.agui._translator import AGUITranslator
from orxtra.identity import resolve_caller_principal
from orxtra.protocols import TrustTier
from orxtra.services import get_run

if TYPE_CHECKING:
    import asyncpg
    from fastware import Request
    from orxtra.protocols import AuthContext, PrincipalStorage

log = logging.getLogger(__name__)

# All AG-UI event type values that the broadcaster may emit.
_AG_UI_EVENT_TYPES: list[str] = [member.value for member in EventType]

# Run statuses that indicate the run will never produce more events.
_RUN_TERMINAL_STATUSES: frozenset[str] = frozenset({
    "completed", "failed", "aborted",
})


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


async def _read_run_status(
    pool: asyncpg.Pool,
    run_id: UUID,
) -> str | None:
    """Read just the run status -- cheap single-column query.

    Returns None when the run does not exist (the caller has already been
    access-checked, so this is a benign race).
    """
    return await pool.fetchval(  # type: ignore[no-any-return]
        "SELECT status FROM runs WHERE id = $1", run_id,
    )


def create_agui_router(
    *,
    pool: asyncpg.Pool | None,
    principal_storage: PrincipalStorage | None,
    subscribe_run: Any = None,
) -> tuple[Router, _BroadcasterRegistry]:
    """Create a Router with an SSE route for AG-UI event streaming.

    Returns (router, registry) so the caller can wire per-run broadcasters
    into active run event delivery. Use ``registry.get_or_create(run_id)``
    to obtain the broadcaster for a given run.

    ``pool`` and ``principal_storage`` back the per-run access check: the
    caller's principal is resolved against ``principal_storage`` and the run's
    owner is loaded via ``pool``. The compositor injects both from the
    dispatch context; they are declared explicitly (no service-locator lookup).

    ``subscribe_run`` is an optional callable ``(run_id: UUID, transport_sink,
    overseer_sink) -> Callable[[], None] | None``. When the run is active, it
    registers both sinks on the scheduler and returns an unsubscribe closure.
    When the run is not active (completed or unknown), it returns None.
    """
    registry = _BroadcasterRegistry()
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

        try:
            run_uuid = UUID(run_id)
        except ValueError:
            return TextResponse("run_id must be a valid UUID", status=400)

        # -- Per-run channel --
        run_broadcaster = registry.subscribe(run_uuid)

        # Each SSE connection gets its own translator and sinks so
        # concurrent clients have independent framing state.
        translator = AGUITranslator(
            thread_id=thread_id,
            run_id=run_id,
            thinking_visibility=thinking,
        )

        async def push_event(event: BaseEvent) -> None:
            encoded = encoder.encode(event)
            # Broadcaster.broadcast expects (event_type, data).
            # We use the encoded SSE string directly as data.
            run_broadcaster.broadcast("message", encoded)

        transport_sink = AGUITransportSink(translator, push_event)
        overseer_sink = AGUIOverseerSink(translator, push_event)

        # Subscribe to live events if the run is active.
        unsubscribe_run: Any = None
        if subscribe_run is not None:
            unsubscribe_run = subscribe_run(
                run_uuid, transport_sink, overseer_sink,
            )

        log.info(
            "AG-UI SSE client connected for run_id=%s thread_id=%s active=%s",
            run_id,
            thread_id,
            unsubscribe_run is not None,
        )

        # Wrap the broadcaster's SSE stream to add registry cleanup on
        # disconnect and terminal marking.
        sse_response = await run_broadcaster.stream(request)

        async def _wrapped_generator() -> AsyncGenerator[str, None]:
            try:
                async for chunk in sse_response.generator:
                    yield chunk
            finally:
                # -- Cleanup on SSE disconnect --
                # Unsubscribe live event sinks first.
                if unsubscribe_run is not None:
                    try:
                        unsubscribe_run()
                    except Exception:  # noqa: BLE001
                        log.debug(
                            "unsubscribe_run failed for run %s",
                            run_uuid,
                            exc_info=True,
                        )
                # Check run status to decide whether the channel is terminal.
                if pool is not None:
                    try:
                        status = await _read_run_status(pool, run_uuid)
                        if status is not None and status in _RUN_TERMINAL_STATUSES:
                            registry.mark_terminal(run_uuid)
                    except Exception:  # noqa: BLE001
                        log.debug(
                            "failed to read run status for terminal check "
                            "(run %s); skipping",
                            run_uuid,
                            exc_info=True,
                        )
                registry.unsubscribe(run_uuid)
                log.info(
                    "AG-UI SSE client disconnected for run_id=%s", run_id,
                )

        return StreamResponse(
            _wrapped_generator(),
            content_type=sse_response.content_type,
            headers=sse_response.headers,
        )

    router.add_route("GET", "/events", events_handler)

    return router, registry
