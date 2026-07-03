"""PostgreSQL test fixtures using testcontainers.

Provides session-scoped PG container and per-test connection pool
with the full orxtra schema (trace + dispatch + auth) applied via
the pgdesign-generated executor.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

import pytest

if TYPE_CHECKING:
    import types
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
# asyncpg adapter for the generated schema executor's AsyncConnection protocol
# ---------------------------------------------------------------------------
# asyncpg.Connection doesn't match the executor's protocol exactly:
# - Transaction doesn't have execute(); queries go through the connection
# - fetch() returns list[Record], not list[dict]
# This adapter bridges the gap for test fixtures.


class _AsyncpgTx:
    """Adapter wrapping asyncpg transaction to satisfy AsyncTransaction."""

    def __init__(self, conn: asyncpg.Connection[Any]) -> None:
        self._conn = conn
        self._tx: Any = None

    async def __aenter__(self) -> Self:
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self._tx.rollback()
        else:
            await self._tx.commit()

    async def execute(self, query: str) -> None:
        await self._conn.execute(query)


class _AsyncpgAdapter:
    """Adapter wrapping asyncpg.Connection to satisfy AsyncConnection."""

    def __init__(self, conn: asyncpg.Connection[Any]) -> None:
        self._conn = conn

    async def execute(self, query: str) -> None:
        await self._conn.execute(query)

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        rows = await self._conn.fetch(query)
        return [dict(r) for r in rows]

    def transaction(self) -> _AsyncpgTx:
        return _AsyncpgTx(self._conn)


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


# pg_uuidv7 extension stub: PG16 testcontainers image lacks pg_uuidv7.
# The generated DDL expects CREATE EXTENSION pg_uuidv7; this stub replaces
# it with a function mapping uuid_generate_v7() to gen_random_uuid().
# TraceWriter always supplies explicit UUIDs from Python; the DEFAULT
# never fires in practice, but CREATE TABLE validates function existence.
_PG_UUIDV7_STUB = """\
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
    SELECT gen_random_uuid();
$$ LANGUAGE sql;
"""


@pytest.fixture
async def pg_pool(
    pg_container: Any,  # noqa: ANN401
) -> AsyncIterator[asyncpg.Pool]:
    """Create an asyncpg pool with the full orxtra schema."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
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
        await conn.execute("CREATE SCHEMA public")

        # Apply the full schema (trace -> dispatch -> auth) via the
        # generated executor, substituting the pg_uuidv7 extension
        # with a gen_random_uuid() stub for testcontainers PG.
        adapter = _AsyncpgAdapter(conn)
        result = await schema_execute(
            adapter,
            idempotent=False,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
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
