"""RunManager -- maps active run IDs to their Scheduler instances.

Allows SSE clients (AG-UI) to subscribe to a running run's event streams
by registering transport and overseer sinks on the scheduler. Subscribing
to a run that is not active (not registered) returns None, signaling the
caller to serve a completed-run snapshot instead.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

if TYPE_CHECKING:
    from collections.abc import Callable

    from orxtra.protocols import EventSink, OverseerEvent
    from orxtra.scheduler import Scheduler
    from orxtra.transport import TransportEvent


class RunManager:
    """Instance-scoped registry of active run schedulers.

    Held on the DispatchContext and constructed during API lifespan.
    """

    def __init__(self) -> None:
        self._schedulers: dict[UUID, Scheduler] = {}

    def register_run(self, run_id: UUID, scheduler: Scheduler) -> None:
        """Register a scheduler for an active run."""
        self._schedulers[run_id] = scheduler

    def deregister_run(self, run_id: UUID) -> None:
        """Remove the scheduler for a completed/failed run."""
        self._schedulers.pop(run_id, None)

    def subscribe(
        self,
        run_id: UUID,
        transport_sink: EventSink[TransportEvent],
        overseer_sink: EventSink[OverseerEvent],
    ) -> Callable[[], None] | None:
        """Subscribe sinks to a live run.

        Returns an unsubscribe closure if the run is active, or None if the
        run is not registered (completed/not started).
        """
        scheduler = self._schedulers.get(run_id)
        if scheduler is None:
            return None

        scheduler.add_transport_sink(transport_sink)
        scheduler.add_overseer_sink(overseer_sink)

        def _unsubscribe() -> None:
            scheduler.remove_transport_sink(transport_sink)
            scheduler.remove_overseer_sink(overseer_sink)

        return _unsubscribe

    def is_active(self, run_id: UUID) -> bool:
        """Check whether a run is currently registered."""
        return run_id in self._schedulers

    def active_run_ids(self) -> frozenset[UUID]:
        """Return the set of currently active run IDs."""
        return frozenset(self._schedulers.keys())
