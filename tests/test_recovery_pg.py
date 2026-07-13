"""Real-PG crash-recovery tests.

Exercises the actual ``reclaim_interrupted`` / ``clean_orphaned`` recovery
functions against a live PostgreSQL database (via testcontainers), rather
than the mocked pool used by ``trace/tests/test_recovery.py``. Every scheduler
recovery test mocks these functions, so no test previously drove the real DDL
path -- which is exactly how a missing ``events.principal_id`` slipped in.

``events.principal_id`` is NOT NULL (RESTRICT FK into ``principals``). Recovery
emits a ``crash_recovery`` event, so it MUST attribute that event to the SYSTEM
principal -- the machinery acting on the operator's behalf. These tests assert
that attribution and cover the unseeded-system-principal error path.
"""
from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxtra.identity import PgPrincipalStorage
from orxtra.protocols import (
    KIND_RUN,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
)
from orxtra.trace import TraceWriter, clean_orphaned, reclaim_interrupted

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import asyncpg

pytestmark = skip_no_docker


# -- Helpers ------------------------------------------------------------------


async def _seed_system(pool: asyncpg.Pool) -> uuid6.UUID:
    """Seed the singleton system principal and return its id."""
    storage = PgPrincipalStorage(pool)
    system = await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
    )
    return system.id


async def _create_run(
    pool: asyncpg.Pool, creator_id: uuid6.UUID,
) -> tuple[uuid6.UUID, uuid6.UUID]:
    """Create a running run. Returns ``(run_id, run_principal_id)``."""
    storage = PgPrincipalStorage(pool)
    writer = TraceWriter(pool)
    run_id = uuid6.uuid7()
    run_principal = await storage.mint_principal(KIND_RUN, run_id, None)
    await writer.create_run(
        intent="recovery test",
        config={},
        autonomy_level="full",
        run_id=run_id,
        created_by=creator_id,
    )
    await writer.transition_run(
        run_id, "running", principal_id=run_principal.id,
    )
    return run_id, run_principal.id


# -- reclaim_interrupted ------------------------------------------------------


class TestReclaimInterruptedPg:
    async def test_attributes_crash_recovery_to_system_principal(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """A reclaimed task's crash_recovery event names the system principal."""
        system_id = await _seed_system(pg_pool)
        run_id, run_pid = await _create_run(pg_pool, system_id)
        writer = TraceWriter(pg_pool)
        task_id = await writer.create_task(
            run_id=run_id, parent_task_id=None, name="t", task_type="callable",
        )
        # created -> prechecking makes the task reclaimable.
        await writer.transition_task(
            task_id, "prechecking", principal_id=run_pid,
        )

        reclaimed = await reclaim_interrupted(pg_pool)

        assert reclaimed == 1
        status = await pg_pool.fetchval(
            "SELECT status FROM tasks WHERE id = $1", task_id,
        )
        assert status == "cancelled"
        row = await pg_pool.fetchrow(
            "SELECT principal_id, data FROM events"
            " WHERE event_type = 'crash_recovery' AND task_id = $1",
            task_id,
        )
        assert row is not None
        assert row["principal_id"] == system_id
        assert json.loads(row["data"])["action"] == "reclaim_interrupted"


# -- clean_orphaned -----------------------------------------------------------


class TestCleanOrphanedPg:
    async def test_attributes_crash_recovery_to_system_principal(
        self, pg_pool: asyncpg.Pool,
    ) -> None:
        """An orphaned run's crash_recovery event names the system principal."""
        system_id = await _seed_system(pg_pool)
        run_id, _ = await _create_run(pg_pool, system_id)
        # Run is 'running' with no advisory lock held -> orphaned.

        cleaned = await clean_orphaned(pg_pool)

        assert cleaned == 1
        status = await pg_pool.fetchval(
            "SELECT status FROM runs WHERE id = $1", run_id,
        )
        assert status == "failed"
        row = await pg_pool.fetchrow(
            "SELECT principal_id, data FROM events"
            " WHERE event_type = 'crash_recovery' AND run_id = $1",
            run_id,
        )
        assert row is not None
        assert row["principal_id"] == system_id
        assert json.loads(row["data"])["action"] == "clean_orphaned"
