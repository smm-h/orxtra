from __future__ import annotations

from typing import TYPE_CHECKING, Self

import pytest
from orxt.trace import TraceWriter

if TYPE_CHECKING:
    from collections.abc import ItemsView, KeysView, ValuesView


class MockRecord:
    """Mock asyncpg Record that supports key access and dict() conversion."""

    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def values(self) -> ValuesView[object]:
        return self._data.values()

    def items(self) -> ItemsView[str, object]:
        return self._data.items()


class MockTransaction:
    """No-op async context manager standing in for asyncpg Transaction."""

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class MockConnection:
    """Mock asyncpg connection that records SQL calls and returns canned results."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self._fetch_results: list[list[dict[str, object]]] = []
        self._fetchrow_results: list[dict[str, object] | None] = []
        self._fetchval_results: list[object] = []

    # -- queue helpers --

    def queue_fetch(
        self, rows: list[dict[str, object]],
    ) -> None:
        self._fetch_results.append(rows)

    def queue_fetchrow(
        self, row: dict[str, object] | None,
    ) -> None:
        self._fetchrow_results.append(row)

    def queue_fetchval(self, value: object) -> None:
        self._fetchval_results.append(value)

    # -- asyncpg-compatible interface --

    async def execute(
        self, sql: str, *args: object,
    ) -> str:
        self.executed.append((sql, args))
        return "DONE"

    async def fetch(
        self, sql: str, *args: object,
    ) -> list[MockRecord]:
        self.executed.append((sql, args))
        if self._fetch_results:
            rows = self._fetch_results.pop(0)
            return [MockRecord(r) for r in rows]
        return []

    async def fetchrow(
        self, sql: str, *args: object,
    ) -> MockRecord | None:
        self.executed.append((sql, args))
        if self._fetchrow_results:
            row = self._fetchrow_results.pop(0)
            return MockRecord(row) if row is not None else None
        return None

    async def fetchval(
        self, sql: str, *args: object,
    ) -> object | None:
        self.executed.append((sql, args))
        if self._fetchval_results:
            return self._fetchval_results.pop(0)
        return None

    def transaction(self) -> MockTransaction:
        return MockTransaction()


class MockPoolAcquire:
    """Async context manager that yields a MockConnection."""

    def __init__(self, conn: MockConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> MockConnection:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        pass


class MockPool:
    """Mock asyncpg Pool that uses a shared MockConnection."""

    def __init__(self) -> None:
        self.conn = MockConnection()

    def acquire(self) -> MockPoolAcquire:
        return MockPoolAcquire(self.conn)

    async def execute(
        self, sql: str, *args: object,
    ) -> str:
        return await self.conn.execute(sql, *args)

    async def fetch(
        self, sql: str, *args: object,
    ) -> list[MockRecord]:
        return await self.conn.fetch(sql, *args)

    async def fetchrow(
        self, sql: str, *args: object,
    ) -> MockRecord | None:
        return await self.conn.fetchrow(sql, *args)

    async def fetchval(
        self, sql: str, *args: object,
    ) -> object | None:
        return await self.conn.fetchval(sql, *args)


@pytest.fixture
def mock_pool() -> MockPool:
    return MockPool()


@pytest.fixture
def writer(mock_pool: MockPool) -> TraceWriter:
    return TraceWriter(mock_pool)  # type: ignore[arg-type]
