"""PG integration tests for the orxtra db command group.

Tests db init (idempotent schema creation) and db verify (schema
verification) against a real PostgreSQL database via testcontainers.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import types

    import asyncpg

pytestmark = skip_no_docker


class _AsyncpgTx:
    """Adapter wrapping asyncpg transaction for the schema executor."""

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
    """Adapter wrapping asyncpg.Connection for the schema executor."""

    def __init__(self, conn: asyncpg.Connection[Any]) -> None:
        self._conn = conn

    async def execute(self, query: str) -> None:
        await self._conn.execute(query)

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        rows = await self._conn.fetch(query)
        return [dict(r) for r in rows]

    def transaction(self) -> _AsyncpgTx:
        return _AsyncpgTx(self._conn)


_PG_UUIDV7_STUB = """\
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
    SELECT gen_random_uuid();
$$ LANGUAGE sql;
"""


async def test_db_init_creates_schema_on_empty_db(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """db init on an empty database creates all schema objects."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
        execute,
        verify,
    )

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")

        adapter = _AsyncpgAdapter(conn)

        result = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
        )
        assert not result.errors, (
            f"Schema init errors: {result.errors}"
        )
        assert len(result.executed) > 0

        vresult = await verify(adapter)
        assert len(vresult.missing) == 0, (
            f"Missing after init: {vresult.missing}"
        )
        assert len(vresult.present) > 0
    finally:
        await conn.close()


async def test_db_init_is_idempotent(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """Running db init twice produces no errors."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
        execute,
    )

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")

        adapter = _AsyncpgAdapter(conn)

        r1 = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
        )
        assert not r1.errors

        r2 = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
        )
        assert not r2.errors
    finally:
        await conn.close()


async def test_db_verify_detects_missing_on_empty_db(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """db verify on an empty database reports missing objects."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
        verify,
    )

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")

        adapter = _AsyncpgAdapter(conn)

        vresult = await verify(adapter)
        assert len(vresult.missing) > 0
        assert ("schemas", "public") in vresult.present
    finally:
        await conn.close()


async def test_db_verify_zero_missing_after_init(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """db verify reports zero missing after db init."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
        execute,
        verify,
    )

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")

        adapter = _AsyncpgAdapter(conn)

        result = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
        )
        assert not result.errors

        vresult = await verify(adapter)
        assert len(vresult.missing) == 0
    finally:
        await conn.close()
