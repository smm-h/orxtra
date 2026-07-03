"""PG integration tests for shared schema verification.

Tests verify_schema() against real PostgreSQL via testcontainers:
- empty DB raises SchemaError with actionable message
- initialized DB returns silently
"""
from __future__ import annotations

from typing import Any

import pytest
from orxtra.services import (
    PG_UUIDV7_STUB,
    AsyncpgAdapter,
    SchemaError,
    verify_schema,
)

from tests.pg_fixtures import skip_no_docker

pytestmark = skip_no_docker


async def test_verify_schema_raises_on_empty_db(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """verify_schema on an empty DB raises SchemaError with actionable msg."""
    import asyncpg  # noqa: PLC0415

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    pool = await asyncpg.create_pool(url)
    try:
        # Ensure empty schema
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")

        with pytest.raises(SchemaError) as exc_info:
            await verify_schema(pool)

        msg = str(exc_info.value)
        assert "Database schema is incomplete" in msg
        assert "orxtra db init" in msg
        assert "orxtra db migrate apply" in msg
        # Should mention at least one real missing object
        assert "tables." in msg
    finally:
        await pool.close()


async def test_verify_schema_passes_after_init(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """verify_schema returns silently after db init."""
    import asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
        execute,
    )

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    pool = await asyncpg.create_pool(url)
    try:
        # Init the schema
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")
            adapter = AsyncpgAdapter(conn)
            result = await execute(
                adapter,
                idempotent=True,
                extension_stubs={"pg_uuidv7": PG_UUIDV7_STUB},
            )
            assert not result.errors

        # verify_schema should return silently
        await verify_schema(pool)
    finally:
        await pool.close()


async def test_verify_schema_error_message_is_actionable(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """The SchemaError message names specific missing objects."""
    import asyncpg  # noqa: PLC0415

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    pool = await asyncpg.create_pool(url)
    try:
        async with pool.acquire() as conn:
            await conn.execute("DROP SCHEMA public CASCADE")
            await conn.execute("CREATE SCHEMA public")

        with pytest.raises(SchemaError) as exc_info:
            await verify_schema(pool)

        msg = str(exc_info.value)
        # The message should contain specific object names so the
        # operator knows what's missing.
        assert "runs" in msg
    finally:
        await pool.close()
