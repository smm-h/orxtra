from __future__ import annotations

import asyncio


class LivenessMonitor:
    """Monitors streaming response liveness by tracking time between events.

    Call ``record_event()`` each time an SSE event arrives. Call ``check()``
    to get the current status: ``"healthy"``, ``"warning"``, ``"stuck"``, or
    ``None`` if no event has been recorded yet.
    """

    def __init__(
        self,
        health_threshold: float = 30.0,
        stuck_threshold: float = 120.0,
    ) -> None:
        self._health_threshold = health_threshold
        self._stuck_threshold = stuck_threshold
        self._last_event_time: float | None = None

    def record_event(self) -> None:
        """Call on each SSE event received."""
        self._last_event_time = asyncio.get_event_loop().time()

    def check(self) -> str | None:
        """Returns 'healthy', 'warning', 'stuck', or None (no events yet)."""
        if self._last_event_time is None:
            return None
        elapsed = asyncio.get_event_loop().time() - self._last_event_time
        if elapsed > self._stuck_threshold:
            return "stuck"
        if elapsed > self._health_threshold:
            return "warning"
        return "healthy"

    @property
    def elapsed(self) -> float | None:
        """Seconds since the last event, or None if no event recorded."""
        if self._last_event_time is None:
            return None
        return asyncio.get_event_loop().time() - self._last_event_time

    def reset(self) -> None:
        """Clear recorded state."""
        self._last_event_time = None
