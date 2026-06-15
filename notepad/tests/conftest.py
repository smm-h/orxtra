from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

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


class MockConnection:
    """Mock asyncpg connection that records SQL calls and returns canned results."""

    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self._fetch_results: list[list[dict[str, object]]] = []

    def queue_fetch(
        self, rows: list[dict[str, object]],
    ) -> None:
        self._fetch_results.append(rows)

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


class MockPool:
    """Mock asyncpg Pool that uses a shared MockConnection."""

    def __init__(self) -> None:
        self.conn = MockConnection()

    async def fetch(
        self, sql: str, *args: object,
    ) -> list[MockRecord]:
        return await self.conn.fetch(sql, *args)


@pytest.fixture
def mock_pool() -> MockPool:
    return MockPool()
