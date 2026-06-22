from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from orxtra.trace import (
    RunLockError,
    acquire_run_lock,
    is_lock_stale,
    release_run_lock,
    update_heartbeat,
)

if TYPE_CHECKING:
    from .conftest import MockPool

RUN_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")


class TestAcquireRunLock:
    @pytest.mark.asyncio
    async def test_acquire_run_lock_success(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchval(True)

        await acquire_run_lock(mock_pool, RUN_ID)  # type: ignore[arg-type]

        sqls = [sql for sql, _ in mock_pool.conn.executed]
        assert any("pg_try_advisory_lock" in sql for sql in sqls)

    @pytest.mark.asyncio
    async def test_acquire_run_lock_already_held(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchval(False)

        with pytest.raises(RunLockError):
            await acquire_run_lock(mock_pool, RUN_ID)  # type: ignore[arg-type]


class TestReleaseRunLock:
    @pytest.mark.asyncio
    async def test_release_run_lock(self, mock_pool: MockPool) -> None:
        await release_run_lock(mock_pool, RUN_ID)  # type: ignore[arg-type]

        sqls = [sql for sql, _ in mock_pool.conn.executed]
        assert any("pg_advisory_unlock" in sql for sql in sqls)


class TestUpdateHeartbeat:
    @pytest.mark.asyncio
    async def test_update_heartbeat(self, mock_pool: MockPool) -> None:
        await update_heartbeat(mock_pool, RUN_ID)  # type: ignore[arg-type]

        sqls = [sql for sql, _ in mock_pool.conn.executed]
        assert any("run_heartbeats" in sql for sql in sqls)
        assert any("ON CONFLICT" in sql for sql in sqls)


class TestIsLockStale:
    @pytest.mark.asyncio
    async def test_is_lock_stale_true(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow({"is_stale": True})

        result = await is_lock_stale(mock_pool, RUN_ID, 300.0)  # type: ignore[arg-type]

        assert result is True

    @pytest.mark.asyncio
    async def test_is_lock_stale_false(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow({"is_stale": False})

        result = await is_lock_stale(mock_pool, RUN_ID, 300.0)  # type: ignore[arg-type]

        assert result is False

    @pytest.mark.asyncio
    async def test_is_lock_stale_no_heartbeat(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow(None)

        result = await is_lock_stale(mock_pool, RUN_ID, 300.0)  # type: ignore[arg-type]

        assert result is True
