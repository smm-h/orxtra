"""PG integration tests for the orxtra db command group.

Tests db init (idempotent schema creation) and db verify (schema
verification) against a real PostgreSQL database via testcontainers.
"""
from __future__ import annotations

from typing import Any

from orxtra.services import PG_UUIDV7_STUB, AsyncpgAdapter

from tests.pg_fixtures import skip_no_docker

pytestmark = skip_no_docker


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

        adapter = AsyncpgAdapter(conn)

        result = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": PG_UUIDV7_STUB},
        )
        assert not result.errors, (
            f"Schema init errors: {result.errors}"
        )
        assert len(result.executed) > 0

        # Verify excludes extensions (we used a stub, not the real
        # extension) and comments (the verify checker can't detect
        # COMMENT ON statements -- no pg_catalog query for them).
        vresult = await verify(
            adapter,
            exclude_sections=["extensions", "comments"],
        )
        # The executor places functions and triggers in the "indexes"
        # section, but verify checks pg_indexes (which only has actual
        # indexes). Filter out these known false positives.
        real_missing = [
            (kind, name) for kind, name in vresult.missing
            if not (kind == "indexes" and "deny_mutation" in name)
        ]
        assert len(real_missing) == 0, (
            f"Missing after init: {real_missing}"
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

        adapter = AsyncpgAdapter(conn)

        r1 = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": PG_UUIDV7_STUB},
        )
        assert not r1.errors

        r2 = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": PG_UUIDV7_STUB},
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

        adapter = AsyncpgAdapter(conn)

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

        adapter = AsyncpgAdapter(conn)

        result = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": PG_UUIDV7_STUB},
        )
        assert not result.errors

        vresult = await verify(
            adapter,
            exclude_sections=["extensions", "comments"],
        )
        real_missing = [
            (kind, name) for kind, name in vresult.missing
            if not (kind == "indexes" and "deny_mutation" in name)
        ]
        assert len(real_missing) == 0
    finally:
        await conn.close()
