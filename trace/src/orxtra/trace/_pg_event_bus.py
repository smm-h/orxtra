from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    import asyncpg


class PgEventBus:
    """PostgreSQL LISTEN/NOTIFY implementation of EventBus.

    Wraps asyncpg LISTEN/NOTIFY into the EventBus protocol. Each
    subscription acquires a dedicated connection from the pool to
    hold the LISTEN registration.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        self._listeners: dict[str, asyncpg.Connection] = {}

    async def subscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None:
        """Subscribe to a channel. Acquires a connection for LISTEN."""
        if channel in self._listeners:
            msg = f"already subscribed to channel {channel!r}"
            raise ValueError(msg)

        conn: asyncpg.Connection = await self._pool.acquire()  # type: ignore[assignment]

        def _on_notification(
            conn: asyncpg.Connection,
            pid: int,
            channel: str,
            payload: str,
        ) -> None:
            # asyncpg notifications are sync callbacks; schedule the
            # async callback on the running event loop.
            import asyncio

            asyncio.ensure_future(callback(payload))

        await conn.add_listener(channel, _on_notification)  # type: ignore[arg-type]
        self._listeners[channel] = conn

    async def publish(self, channel: str, payload: str) -> None:
        """Publish a notification on a channel via NOTIFY."""
        await self._pool.execute(f"NOTIFY {channel}, $1", payload)  # noqa: S608

    async def close(self) -> None:
        """Unsubscribe from all channels and release connections."""
        for channel, conn in self._listeners.items():
            await conn.remove_listener(channel, lambda *_a: None)
            await self._pool.release(conn)  # type: ignore[arg-type]
        self._listeners.clear()
