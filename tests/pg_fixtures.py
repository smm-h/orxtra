"""PostgreSQL test fixtures using testcontainers.

Provides session-scoped PG container and per-test connection pool
with the full orxtra trace schema applied.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

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


# Stub for uuid_generate_v7: PG16 lacks pg_uuidv7 extension.
# TraceWriter always supplies explicit UUIDs from Python; the DEFAULT
# never fires, but CREATE TABLE validates function existence.
_UUID_V7_STUB = """\
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
    SELECT gen_random_uuid();
$$ LANGUAGE sql;
"""


@pytest.fixture(scope="session")
def pg_container() -> Iterator[Any]:
    """Start a PostgreSQL 16 container for integration tests."""
    if not _HAS_TESTCONTAINERS:
        pytest.skip("testcontainers not available")
    with PostgresContainer("postgres:16") as pg:
        yield pg


@pytest.fixture
async def pg_pool(pg_container: Any) -> AsyncIterator[asyncpg.Pool]:  # noqa: ANN401
    """Create an asyncpg pool with the full orxtra trace schema."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from orxtra.trace._schema import ALL_CREATE_STATEMENTS  # noqa: PLC0415

    # testcontainers gives psycopg2-style URL; convert to plain postgresql://
    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://"
    )

    pool = await _asyncpg.create_pool(url)

    async with pool.acquire() as conn:
        # Drop all tables so each test starts clean.
        # Disable triggers to avoid FK ordering issues during DROP.
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        # Install uuid_generate_v7 stub.
        await conn.execute(_UUID_V7_STUB)
        # Create all tables.
        for stmt in ALL_CREATE_STATEMENTS:
            await conn.execute(stmt)

    yield pool
    await pool.close()
