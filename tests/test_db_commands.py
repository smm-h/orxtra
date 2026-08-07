"""PG integration tests for the orxtra db command group.

Tests db init (idempotent schema creation) and db verify (schema
verification) against a real PostgreSQL database via testcontainers.
"""
from __future__ import annotations

from typing import Any

from orxtra.services import AsyncpgAdapter

from tests.pg_fixtures import skip_no_docker

pytestmark = skip_no_docker


async def test_db_init_creates_schema_on_empty_db(
    pg_container: Any,
) -> None:
    """db init on an empty database creates all schema objects."""
    import asyncpg as _asyncpg
    from orxtra.services._generated.schema_executor import (
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
            exclude_sections=["comments"],
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
    pg_container: Any,
) -> None:
    """Running db init twice produces no errors."""
    import asyncpg as _asyncpg
    from orxtra.services._generated.schema_executor import (
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
        )
        assert not r1.errors

        r2 = await execute(
            adapter,
            idempotent=True,
        )
        assert not r2.errors
    finally:
        await conn.close()


async def test_db_verify_detects_missing_on_empty_db(
    pg_container: Any,
) -> None:
    """db verify on an empty database reports missing objects."""
    import asyncpg as _asyncpg
    from orxtra.services._generated.schema_executor import (
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


async def test_db_init_seeds_system_principal(
    pg_container: Any,
) -> None:
    """After schema creation, seeding mints the singleton system principal.

    Mirrors the seeding wired into ``orxtra db init`` (cli/_db.py): schema
    is created, then ``PgPrincipalStorage(pool).mint_principal`` seeds the
    system principal. Asserts the row exists and is idempotent.
    """
    import asyncpg as _asyncpg
    from orxtra.identity import PgPrincipalStorage
    from orxtra.protocols import KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF
    from orxtra.services._generated.schema_executor import (
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
        result = await execute(
            adapter,
            idempotent=True,
        )
        assert not result.errors
    finally:
        await conn.close()

    pool = await _asyncpg.create_pool(url)
    try:
        storage = PgPrincipalStorage(pool)
        seeded = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        assert seeded.kind == KIND_SYSTEM
        assert seeded.external_ref == SYSTEM_PRINCIPAL_EXTERNAL_REF
        assert seeded.display_name == "system"

        # The row is present and resolvable by ref.
        by_ref = await storage.get_principal_by_ref(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF,
        )
        assert by_ref is not None
        assert by_ref.id == seeded.id

        # Seeding again is idempotent -- same row, no duplicate.
        again = await storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        assert again.id == seeded.id
        system_rows = await storage.list_principals(KIND_SYSTEM)
        assert len(system_rows) == 1
    finally:
        await pool.close()


async def test_db_verify_zero_missing_after_init(
    pg_container: Any,
) -> None:
    """db verify reports zero missing after db init."""
    import asyncpg as _asyncpg
    from orxtra.services._generated.schema_executor import (
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
        )
        assert not result.errors

        vresult = await verify(
            adapter,
            exclude_sections=["comments"],
        )
        real_missing = [
            (kind, name) for kind, name in vresult.missing
            if not (kind == "indexes" and "deny_mutation" in name)
        ]
        assert len(real_missing) == 0
    finally:
        await conn.close()


async def test_verify_schema_objects_filters_false_positives(
    pg_container: Any,
) -> None:
    """The shared helper used by ``orxtra db verify`` reports zero missing
    after init.

    Regression: ``cmd_db_verify`` used to call the raw generated ``verify()``
    without filtering the sections/entries that have no reliable existence
    check (COMMENT statements and ``deny_mutation`` triggers/functions), so
    ``orxtra db verify`` exited 1 on a fully-initialized schema. It now routes
    through ``verify_schema_objects``, which applies the same filtering as
    ``verify_schema``.
    """
    import asyncpg as _asyncpg
    from orxtra.services import verify_schema_objects
    from orxtra.services._generated.schema_executor import execute

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")

        adapter = AsyncpgAdapter(conn)
        exec_result = await execute(adapter, idempotent=True)
        assert not exec_result.errors

        present, missing = await verify_schema_objects(adapter)
        assert missing == [], f"Unexpected missing objects: {missing}"
        assert len(present) > 0
    finally:
        await conn.close()
