"""PostgreSQL test fixtures using testcontainers.

Provides session-scoped PG container and per-test connection pool
with the full orxtra schema (trace + dispatch + auth) applied via
the pgdesign-generated executor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest
from orxtra.services import PG_UUIDV7_STUB, AsyncpgAdapter

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Iterator

    import asyncpg

# Guard: skip gracefully when docker/testcontainers unavailable.
try:
    from testcontainers.postgres import PostgresContainer

    _HAS_TESTCONTAINERS = True
except ImportError:
    _HAS_TESTCONTAINERS = False

skip_no_docker = pytest.mark.skipif(
    not _HAS_TESTCONTAINERS,
    reason="testcontainers[postgres] not installed or docker unavailable",
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="session")
def pg_container() -> Iterator[Any]:
    """Start a PostgreSQL 16 container for integration tests."""
    if not _HAS_TESTCONTAINERS:
        pytest.skip("testcontainers not available")
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pg_pool(
    pg_container: Any,
) -> AsyncIterator[asyncpg.Pool]:
    """Create an asyncpg pool with the full orxtra schema."""
    import asyncpg as _asyncpg
    from _generated.schema_executor import (
        execute as schema_execute,
    )

    # testcontainers gives psycopg2-style URL; convert to plain
    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://"
    )

    pool = await _asyncpg.create_pool(url)

    async with pool.acquire() as conn:
        # Drop all tables so each test starts clean.
        await conn.execute("DROP SCHEMA public CASCADE")

        # Apply the full schema (trace -> dispatch -> auth) via the
        # generated executor, substituting the pg_uuidv7 extension
        # with a gen_random_uuid() stub for testcontainers PG.
        adapter = AsyncpgAdapter(conn)
        result = await schema_execute(
            adapter,
            idempotent=False,
            extension_stubs={"pg_uuidv7": PG_UUIDV7_STUB},
        )
        if result.errors:
            err_msg = "; ".join(
                f"{kind}.{name}: {err}"
                for kind, name, err in result.errors
            )
            msg = f"Schema creation failed: {err_msg}"
            raise RuntimeError(msg)

    yield pool
    await pool.close()
