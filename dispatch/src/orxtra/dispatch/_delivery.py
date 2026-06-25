from __future__ import annotations

import asyncio


class TransientEventDelivery:
    """In-memory event delivery using asyncio Futures.

    Implements the ``EventDelivery`` protocol from ``orxtra.protocols``.
    Same semantics as the scheduler's ``EventRegistry``: fire resolves
    all current waiters, events fired before any waiter registers are
    silently lost (no replay), and multiple waiters on the same event
    all receive the same payload.
    """

    def __init__(self) -> None:
        self._listeners: dict[
            str, list[asyncio.Future[dict[str, object] | None]]
        ] = {}

    async def fire(
        self,
        event_name: str,
        payload: dict[str, object] | None = None,
    ) -> None:
        futures = self._listeners.pop(event_name, [])
        for fut in futures:
            if not fut.done():
                fut.set_result(payload)

    async def wait_for(
        self,
        event_name: str,
        *,
        deadline_seconds: float,
    ) -> dict[str, object] | None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, object] | None] = loop.create_future()
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=deadline_seconds)
        except TimeoutError:
            return None
