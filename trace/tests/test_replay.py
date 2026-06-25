"""Tests for the replay() function -- both PG reader and InMemoryBackend."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from orxtra.trace import replay
from orxtra.trace._memory_backend import InMemoryBackend

if TYPE_CHECKING:
    from .conftest import MockPool

EVENT_ID_1 = UUID("01900000-0000-7000-8000-000000000001")
EVENT_ID_2 = UUID("01900000-0000-7000-8000-000000000002")
EVENT_ID_3 = UUID("01900000-0000-7000-8000-000000000003")
RUN_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
TASK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


# ── PG reader tests ──


class TestReplayPg:
    @pytest.mark.asyncio
    async def test_replay_basic(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "id": EVENT_ID_1,
                "run_id": RUN_ID,
                "task_id": None,
                "event_type": "run_transition",
                "source": "internal",
                "data": {"old_status": "created", "new_status": "running"},
                "created_at": NOW,
            },
        ])

        result = await replay(mock_pool)  # type: ignore[arg-type]

        assert len(result) == 1
        assert result[0]["id"] == EVENT_ID_1
        assert result[0]["event_type"] == "run_transition"
        assert result[0]["source"] == "internal"

    @pytest.mark.asyncio
    async def test_replay_filter_by_event_type(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await replay(  # type: ignore[arg-type]
            mock_pool, event_types=["task_transition"]
        )

        assert result == []
        _sql, args = mock_pool.conn.executed[-1]
        assert ["task_transition"] in args

    @pytest.mark.asyncio
    async def test_replay_filter_by_source(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await replay(  # type: ignore[arg-type]
            mock_pool, source="agent"
        )

        assert result == []
        _sql, args = mock_pool.conn.executed[-1]
        assert "agent" in args

    @pytest.mark.asyncio
    async def test_replay_cursor_since_id(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await replay(  # type: ignore[arg-type]
            mock_pool, since_id=EVENT_ID_1
        )

        assert result == []
        _sql, args = mock_pool.conn.executed[-1]
        assert EVENT_ID_1 in args

    @pytest.mark.asyncio
    async def test_replay_limit(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await replay(  # type: ignore[arg-type]
            mock_pool, limit=5
        )

        assert result == []
        _sql, args = mock_pool.conn.executed[-1]
        assert 5 in args


# ── InMemoryBackend tests ──


class TestReplayInMemory:
    @pytest.mark.asyncio
    async def test_replay_all_events(self) -> None:
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "full")

        eid1 = await backend.write_event(run_id, "ev_a", {"k": 1})
        eid2 = await backend.write_event(run_id, "ev_b", {"k": 2})
        eid3 = await backend.write_event(run_id, "ev_c", {"k": 3})

        result = await backend.replay()

        # InMemoryBackend generates events during create_run (run_transition),
        # so there may be more than 3 -- but our 3 should be present.
        ids = [r["id"] for r in result]
        assert eid1 in ids
        assert eid2 in ids
        assert eid3 in ids

    @pytest.mark.asyncio
    async def test_replay_filter_event_types(self) -> None:
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "full")

        await backend.write_event(run_id, "ev_a", {"k": 1})
        eid2 = await backend.write_event(run_id, "ev_b", {"k": 2})
        await backend.write_event(run_id, "ev_a", {"k": 3})

        result = await backend.replay(event_types=["ev_b"])

        assert len(result) == 1
        assert result[0]["id"] == eid2
        assert result[0]["event_type"] == "ev_b"

    @pytest.mark.asyncio
    async def test_replay_filter_source(self) -> None:
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "full")

        await backend.write_event(run_id, "ev_a", {"k": 1}, source="internal")
        eid2 = await backend.write_event(run_id, "ev_b", {"k": 2}, source="agent")

        result = await backend.replay(source="agent")

        assert len(result) == 1
        assert result[0]["id"] == eid2

    @pytest.mark.asyncio
    async def test_replay_cursor_since_id(self) -> None:
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "full")

        eid1 = await backend.write_event(run_id, "ev_a", {"k": 1})
        eid2 = await backend.write_event(run_id, "ev_b", {"k": 2})
        eid3 = await backend.write_event(run_id, "ev_c", {"k": 3})

        result = await backend.replay(since_id=eid2)

        ids = [r["id"] for r in result]
        assert eid1 not in ids
        assert eid2 not in ids
        assert eid3 in ids

    @pytest.mark.asyncio
    async def test_replay_limit(self) -> None:
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "full")

        for i in range(10):
            await backend.write_event(run_id, "ev", {"i": i})

        result = await backend.replay(limit=3)

        assert len(result) == 3
