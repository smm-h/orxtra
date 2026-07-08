"""AG-UI SSE endpoint using fastware's Broadcaster and Router."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ag_ui.core import (
    BaseEvent,
    EventType,
)
from ag_ui.encoder import EventEncoder
from fastware import Router
from fastware.sse import Broadcaster
from orxtra.agui._sinks import AGUIOverseerSink, AGUITransportSink
from orxtra.agui._translator import AGUITranslator

if TYPE_CHECKING:
    from fastware import Request, StreamResponse

log = logging.getLogger(__name__)

# All AG-UI event type values that the broadcaster may emit.
_AG_UI_EVENT_TYPES: list[str] = [member.value for member in EventType]


def _create_broadcaster() -> Broadcaster:
    """Create a Broadcaster pre-registered with all AG-UI event types."""
    return Broadcaster(strict=False, heartbeat_interval=15.0)


def create_agui_router(
    *,
    subscribe_run: Any = None,
) -> tuple[Router, Broadcaster]:
    """Create a Router with an SSE route for AG-UI event streaming.

    Returns (router, broadcaster) so the caller can wire the broadcaster
    into the active run's event delivery.

    ``subscribe_run`` is reserved for Phase 8 integration. When provided,
    it will be called with (run_id, transport_sink, overseer_sink) to
    register sinks with the scheduler for the active run.
    """
    broadcaster = _create_broadcaster()
    encoder = EventEncoder()

    router = Router()

    async def events_handler(request: Request) -> StreamResponse:
        """SSE endpoint at /events?run_id=...&thread_id=..."""
        run_id = request.query("run_id", "")
        thread_id = request.query("thread_id", run_id)
        thinking = request.query("thinking", "silent")

        if not run_id:
            from fastware import TextResponse
            return TextResponse("run_id query parameter is required", status=400)

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
