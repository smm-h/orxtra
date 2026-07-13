from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from orxtra.trace import clean_orphaned, reclaim_interrupted, reevaluate_blocked

if TYPE_CHECKING:
    from .conftest import MockPool

RUN_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
TASK_ID_1 = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
TASK_ID_2 = UUID("bbbbbbbb-cccc-dddd-eeee-ffffffffffff")
# Recovery resolves the system principal id (via a fetchval SELECT) to
# attribute the crash_recovery event; the mocks queue this stand-in.
SYSTEM_PID = UUID("cccccccc-dddd-eeee-ffff-000000000000")


class TestReclaimInterrupted:
    @pytest.mark.asyncio
    async def test_reclaim_interrupted(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch(
            [
                {"id": TASK_ID_1, "run_id": RUN_ID},
                {"id": TASK_ID_2, "run_id": RUN_ID},
            ]
        )
        mock_pool.conn.queue_fetchval(SYSTEM_PID)

        result = await reclaim_interrupted(mock_pool)  # type: ignore[arg-type]

        assert result == 2
        sqls = [sql for sql, _ in mock_pool.conn.executed]
        assert any("cancelled" in sql for sql in sqls)
        insert_calls = [
            (sql, args)
            for sql, args in mock_pool.conn.executed
            if "INSERT INTO events" in sql
        ]
        assert len(insert_calls) == 2
        # Every crash_recovery event is attributed to the system principal.
        for _sql, args in insert_calls:
            assert SYSTEM_PID in args

    @pytest.mark.asyncio
    async def test_reclaim_interrupted_none(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await reclaim_interrupted(mock_pool)  # type: ignore[arg-type]

        assert result == 0
        update_calls = [
            sql for sql, _ in mock_pool.conn.executed if "UPDATE" in sql
        ]
        assert len(update_calls) == 0


class TestReevaluateBlocked:
    @pytest.mark.asyncio
    async def test_reevaluate_blocked(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([{"id": TASK_ID_1}])

        result = await reevaluate_blocked(mock_pool)  # type: ignore[arg-type]

        assert result == [TASK_ID_1]

    @pytest.mark.asyncio
    async def test_reevaluate_blocked_empty(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await reevaluate_blocked(mock_pool)  # type: ignore[arg-type]

        assert result == []


class TestCleanOrphaned:
    @pytest.mark.asyncio
    async def test_clean_orphaned_acquires_lock(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([{"id": RUN_ID}])
        mock_pool.conn.queue_fetchval(SYSTEM_PID)  # system principal resolution
        mock_pool.conn.queue_fetchval(True)  # pg_try_advisory_lock

        result = await clean_orphaned(mock_pool)  # type: ignore[arg-type]

        assert result == 1
        executed = mock_pool.conn.executed
        sqls = [sql for sql, _ in executed]
        assert any("status = 'failed'" in sql for sql in sqls)
        insert_calls = [
            (sql, args)
            for sql, args in executed
            if "INSERT INTO events" in sql
        ]
        assert len(insert_calls) == 1
        # The crash_recovery event is attributed to the system principal.
        assert SYSTEM_PID in insert_calls[0][1]
        assert any("pg_advisory_unlock" in sql for sql in sqls)

    @pytest.mark.asyncio
    async def test_clean_orphaned_lock_held(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([{"id": RUN_ID}])
        mock_pool.conn.queue_fetchval(SYSTEM_PID)  # system principal resolution
        mock_pool.conn.queue_fetchval(False)  # pg_try_advisory_lock fails

        result = await clean_orphaned(mock_pool)  # type: ignore[arg-type]

        assert result == 0

    @pytest.mark.asyncio
    async def test_clean_orphaned_none(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await clean_orphaned(mock_pool)  # type: ignore[arg-type]

        assert result == 0
