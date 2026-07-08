from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import asyncpg


class _ChannelState:
    """Per-channel state: the asyncpg connection, notification handler,
    and list of application-level callbacks.
    """

    __slots__ = ("callbacks", "conn", "handler")

    def __init__(
        self,
        conn: asyncpg.Connection,
        handler: Any,
        callbacks: list[Callable[[str], Awaitable[None]]],
    ) -> None:
        self.conn = conn
        self.handler = handler
        self.callbacks = callbacks


class PgEventBus:
    """PostgreSQL LISTEN/NOTIFY implementation of EventBus.

    Wraps asyncpg LISTEN/NOTIFY into the EventBus protocol. Multiple
    callbacks can subscribe to the same channel; a single asyncpg
    LISTEN connection is shared across all callbacks for a channel.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._channels: dict[str, _ChannelState] = {}

    async def subscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Subscribe a callback to a channel.

        The first callback on a channel acquires a connection and
        registers the asyncpg LISTEN. Subsequent callbacks on the
        same channel are appended without additional connections.
        """
        state = self._channels.get(channel)
        if state is not None:
            state.callbacks.append(callback)
            return

        # First subscriber on this channel -- acquire connection
        # and register the asyncpg listener.
        conn: asyncpg.Connection = await self._pool.acquire()  # type: ignore[assignment]
        callbacks: list[Callable[[str], Awaitable[None]]] = [callback]

        def _on_notification(
            _conn: asyncpg.Connection,
            _pid: int,
            _channel: str,
            payload: str,
        ) -> None:
            # asyncpg notifications are sync callbacks; fan-out to
            # all registered async callbacks on the running loop.
            for cb in callbacks:
                # Fire-and-forget fan-out; the loop keeps the task alive
                # and callback errors surface via the loop exception handler.
                asyncio.ensure_future(cb(payload))  # noqa: RUF006

        await conn.add_listener(channel, _on_notification)  # type: ignore[arg-type]
        self._channels[channel] = _ChannelState(conn, _on_notification, callbacks)

    async def unsubscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Remove a specific callback from a channel by identity.

        When the last callback is removed, the asyncpg LISTEN is
        cancelled and the connection is released back to the pool.
        """
        state = self._channels.get(channel)
        if state is None:
            return
        try:
            state.callbacks.remove(callback)
        except ValueError:
            return
        if not state.callbacks:
            await state.conn.remove_listener(channel, state.handler)
            await self._pool.release(state.conn)  # type: ignore[arg-type]
            del self._channels[channel]

    async def publish(self, channel: str, payload: str) -> None:
        """Publish a notification on a channel via NOTIFY."""
        await self._pool.execute(f"NOTIFY {channel}, $1", payload)

    async def close(self) -> None:
        """Unsubscribe from all channels and release connections."""
        for channel, state in self._channels.items():
            await state.conn.remove_listener(channel, state.handler)
            await self._pool.release(state.conn)  # type: ignore[arg-type]
        self._channels.clear()
