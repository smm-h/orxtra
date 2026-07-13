"""Per-run broadcaster registry for AG-UI SSE isolation.

Each run gets its own ``Broadcaster`` so events never leak between SSE
clients watching different runs. The registry manages channel lifecycle:
creation on first subscriber, client counting, and eviction when a channel
is both terminal (run finished) and empty (no connected clients).

A permanently idle registry holds only inert dict entries for runs that
were subscribed to but never marked terminal and whose clients all
disconnected. These entries are a bounded leak -- the broadcaster has no
connected clients and never receives events -- and are cleaned on the
next ``subscribe`` call's sweep pass.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from uuid import UUID

from fastware.sse import Broadcaster

__all__: list[str] = []

log = logging.getLogger(__name__)


def _create_broadcaster() -> Broadcaster:
    """Create a Broadcaster for a single run channel."""
    return Broadcaster(strict=False, heartbeat_interval=15.0)


@dataclass
class _RunChannel:
    """Per-run broadcaster plus lifecycle state."""

    broadcaster: Broadcaster = field(default_factory=_create_broadcaster)
    client_count: int = 0
    terminal: bool = False


class _BroadcasterRegistry:
    """Manages per-run broadcaster channels.

    Thread safety: this class is used within a single asyncio event loop
    (all access is from async handlers on the same loop). No locking is
    needed because dict mutations and attribute writes are atomic within
    a single coroutine step -- there are no ``await`` points between a
    read and a dependent write.
    """

    def __init__(self) -> None:
        self._channels: dict[UUID, _RunChannel] = {}

    def get_or_create(self, run_id: UUID) -> Broadcaster:
        """Return the broadcaster for *run_id*, creating the channel if absent.

        Does NOT modify the client count -- use ``subscribe`` to register
        a client.
        """
        channel = self._channels.get(run_id)
        if channel is None:
            channel = _RunChannel()
            self._channels[run_id] = channel
            log.debug("created channel for run %s", run_id)
        return channel.broadcaster

    def subscribe(self, run_id: UUID) -> Broadcaster:
        """Register a client for *run_id* and return the broadcaster.

        Creates the channel if absent. Increments the client count. Also
        sweeps stale channels (terminal + empty) to bound the leak from
        the "last client left before run ended" window.
        """
        self._sweep()
        broadcaster = self.get_or_create(run_id)
        self._channels[run_id].client_count += 1
        log.debug(
            "subscribed to run %s (clients: %d)",
            run_id,
            self._channels[run_id].client_count,
        )
        return broadcaster

    def unsubscribe(self, run_id: UUID) -> None:
        """Unregister a client from *run_id*.

        If the client count reaches zero AND the channel is terminal,
        the channel is evicted (broadcaster disconnected, entry removed).
        If not terminal, the channel stays for reconnection.
        """
        channel = self._channels.get(run_id)
        if channel is None:
            return
        channel.client_count = max(0, channel.client_count - 1)
        log.debug(
            "unsubscribed from run %s (clients: %d, terminal: %s)",
            run_id,
            channel.client_count,
            channel.terminal,
        )
        if channel.client_count == 0 and channel.terminal:
            self._evict(run_id)

    def mark_terminal(self, run_id: UUID) -> None:
        """Mark a run's channel as terminal (run finished).

        If the channel exists and the client count is already zero, the
        channel is evicted immediately.
        """
        channel = self._channels.get(run_id)
        if channel is None:
            return
        channel.terminal = True
        log.debug(
            "marked run %s terminal (clients: %d)", run_id, channel.client_count,
        )
        if channel.client_count == 0:
            self._evict(run_id)

    def has_channel(self, run_id: UUID) -> bool:
        """Check whether a channel exists for *run_id*."""
        return run_id in self._channels

    def client_count(self, run_id: UUID) -> int:
        """Return the client count for *run_id*, or 0 if no channel."""
        channel = self._channels.get(run_id)
        return channel.client_count if channel is not None else 0

    def _sweep(self) -> None:
        """Evict all channels that are terminal with no clients."""
        stale = [
            rid
            for rid, ch in self._channels.items()
            if ch.terminal and ch.client_count == 0
        ]
        for rid in stale:
            self._evict(rid)

    def _evict(self, run_id: UUID) -> None:
        """Remove a channel from the registry."""
        if run_id in self._channels:
            del self._channels[run_id]
            log.debug("evicted channel for run %s", run_id)
