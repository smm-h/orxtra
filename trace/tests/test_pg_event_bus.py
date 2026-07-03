"""Tests for PgEventBus multi-callback, unsubscribe, and close behavior.

Uses lightweight mocks that simulate asyncpg connection/pool behavior
without requiring a live database.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from orxtra.trace._pg_event_bus import PgEventBus


# ---------------------------------------------------------------------------
# Mock infrastructure
# ---------------------------------------------------------------------------


class FakeConnection:
    """Simulates an asyncpg.Connection with add_listener/remove_listener."""

    def __init__(self) -> None:
        self.listeners: dict[str, list[Any]] = {}

    async def add_listener(self, channel: str, callback: Any) -> None:  # noqa: ANN401
        self.listeners.setdefault(channel, []).append(callback)

    async def remove_listener(self, channel: str, callback: Any) -> None:  # noqa: ANN401
        cbs = self.listeners.get(channel, [])
        if callback in cbs:
            cbs.remove(callback)
        if not cbs and channel in self.listeners:
            del self.listeners[channel]


class FakePool:
    """Simulates an asyncpg.Pool for PgEventBus tests.

    Each acquire() returns a fresh FakeConnection. Tracks acquired
    and released connections so tests can verify cleanup.
    """

    def __init__(self) -> None:
        self.acquired: list[FakeConnection] = []
        self.released: list[FakeConnection] = []
        self.execute = AsyncMock()

    async def acquire(self) -> FakeConnection:
        conn = FakeConnection()
        self.acquired.append(conn)
        return conn

    async def release(self, conn: FakeConnection) -> None:
        self.released.append(conn)


@pytest.fixture
def pool() -> FakePool:
    return FakePool()


@pytest.fixture
def bus(pool: FakePool) -> PgEventBus:
    return PgEventBus(pool)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPgEventBusMultiCallback:
    """Multiple callbacks on the same channel share one connection."""

    @pytest.mark.asyncio
    async def test_two_subscribers_one_connection(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        """Subscribing two callbacks to the same channel only acquires
        one connection from the pool."""
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        await bus.subscribe("ch", cb1)
        await bus.subscribe("ch", cb2)

        assert len(pool.acquired) == 1

    @pytest.mark.asyncio
    async def test_different_channels_get_different_connections(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        await bus.subscribe("ch_a", cb1)
        await bus.subscribe("ch_b", cb2)

        assert len(pool.acquired) == 2


class TestPgEventBusUnsubscribe:
    """Unsubscribe removes a specific callback; cleans up when last is gone."""

    @pytest.mark.asyncio
    async def test_unsubscribe_removes_callback(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        await bus.subscribe("ch", cb1)
        await bus.subscribe("ch", cb2)

        await bus.unsubscribe("ch", cb1)

        # Channel still exists with one callback.
        state = bus._channels["ch"]
        assert cb1 not in state.callbacks
        assert cb2 in state.callbacks
        # Connection NOT released yet.
        assert len(pool.released) == 0

    @pytest.mark.asyncio
    async def test_unsubscribe_last_releases_connection(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        cb = AsyncMock()

        await bus.subscribe("ch", cb)
        conn = bus._channels["ch"].conn

        await bus.unsubscribe("ch", cb)

        # Channel entry removed.
        assert "ch" not in bus._channels
        # Connection released back to pool.
        assert conn in pool.released

    @pytest.mark.asyncio
    async def test_unsubscribe_last_calls_remove_listener(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        """When the last callback is removed, the asyncpg listener is
        removed using the REAL handler (not a dummy lambda)."""
        cb = AsyncMock()
        await bus.subscribe("ch", cb)

        conn = bus._channels["ch"].conn
        handler = bus._channels["ch"].handler
        # Verify the handler was registered with asyncpg.
        assert handler in conn.listeners.get("ch", [])

        await bus.unsubscribe("ch", cb)

        # asyncpg listener was removed with the real handler.
        assert "ch" not in conn.listeners

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_channel_is_noop(
        self, bus: PgEventBus,
    ) -> None:
        await bus.unsubscribe("nonexistent", AsyncMock())

    @pytest.mark.asyncio
    async def test_unsubscribe_nonexistent_callback_is_noop(
        self, bus: PgEventBus,
    ) -> None:
        cb_registered = AsyncMock()
        cb_other = AsyncMock()

        await bus.subscribe("ch", cb_registered)
        await bus.unsubscribe("ch", cb_other)

        # Original callback still registered.
        assert cb_registered in bus._channels["ch"].callbacks


class TestPgEventBusClose:
    """close() removes all listeners and releases all connections."""

    @pytest.mark.asyncio
    async def test_close_releases_all_connections(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        cb1 = AsyncMock()
        cb2 = AsyncMock()

        await bus.subscribe("ch_a", cb1)
        await bus.subscribe("ch_b", cb2)

        conns = [bus._channels["ch_a"].conn, bus._channels["ch_b"].conn]

        await bus.close()

        assert len(bus._channels) == 0
        for conn in conns:
            assert conn in pool.released

    @pytest.mark.asyncio
    async def test_close_removes_asyncpg_listeners(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        """close() calls remove_listener with the real handler, not a
        dummy lambda (fixing the pre-existing bug)."""
        cb = AsyncMock()
        await bus.subscribe("ch", cb)

        conn = bus._channels["ch"].conn
        handler = bus._channels["ch"].handler
        assert handler in conn.listeners.get("ch", [])

        await bus.close()

        # The real handler was used for removal.
        assert "ch" not in conn.listeners

    @pytest.mark.asyncio
    async def test_close_idempotent(
        self, bus: PgEventBus, pool: FakePool,
    ) -> None:
        cb = AsyncMock()
        await bus.subscribe("ch", cb)

        await bus.close()
        # Second close should be safe.
        await bus.close()

        assert len(bus._channels) == 0
