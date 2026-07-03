"""Synthetic-migration test for the db migrate workflow.

Tests that the ``orxtra db migrate`` wrapper (pgdesign migrate) correctly
handles all four change categories that Phase 4.1 will exercise:

1. New table
2. Column addition + partial unique index
3. JSONB column addition
4. Enum ADD VALUE

The test:
- Creates the baseline schema (current executor) against a real PG container
- Writes modified TOML schema files with the four deltas
- Runs ``pgdesign migrate generate`` to produce migration files
- Runs ``pgdesign migrate apply`` to apply the migration
- Verifies each change took effect
- Runs ``pgdesign migrate status`` to confirm the migration is recorded

Requires docker (testcontainers). Skipped when unavailable.
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import types

    import asyncpg

pytestmark = skip_no_docker

_SCHEMA_DIR = Path(__file__).resolve().parents[1] / "schema"


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


def _create_modified_schema(tmpdir: Path) -> Path:
    """Copy schema TOML files and add synthetic deltas.

    Modifications use the expanded-table TOML syntax matching the actual
    schema format. Four delta categories:

    1. New table: event_delivery_cursor (in trace.toml)
    2. Column (idempotency_key) + partial unique index on events
    3. JSONB column (config) on sources (in dispatch.toml)
    4. Enum ADD VALUE: credential_type += hmac (in auth.toml)
    """
    schema_copy = tmpdir / "schema"
    schema_copy.mkdir()

    for toml_file in _SCHEMA_DIR.glob("*.toml"):
        dest = schema_copy / toml_file.name
        content = toml_file.read_text()

        if toml_file.name == "trace.toml":
            # Delta 2: Add idempotency_key column to events.
            insertion_point = "[tables.events.columns.created_at]"
            column_addition = (
                "[tables.events.columns.idempotency_key]\n"
                'type = "str"\n'
                "nullable = true\n"
                'comment = "Caller-supplied deduplication key"\n'
                "\n"
            )
            content = content.replace(
                insertion_point,
                column_addition + insertion_point,
            )

            # Delta 2 (cont): Partial unique index.
            idx_insertion = (
                "[tables.events.indexes.idx_events_data_gin]\n"
                'columns = ["data"]\n'
                'method = "gin"\n'
            )
            idx_addition = (
                "\n"
                "[tables.events.indexes."
                "idx_events_idempotency_key]\n"
                'columns = ["idempotency_key"]\n'
                "unique = true\n"
                "where = "
                '"idempotency_key IS NOT NULL"\n'
            )
            content = content.replace(
                idx_insertion,
                idx_insertion + idx_addition,
            )

            # Delta 1: New table at end of file.
            content += (
                "\n# --- Synthetic migration test ---\n"
                "\n"
                "[tables.event_delivery_cursor]\n"
                'comment = "Cursor tracking for event delivery"\n'
                "\n"
                "[tables.event_delivery_cursor.columns.id]\n"
                'type = "id"\n'
                "\n"
                "[tables.event_delivery_cursor.columns"
                ".subscription_action_id]\n"
                'type = "uuid_val"\n'
                "\n"
                "[tables.event_delivery_cursor.columns"
                ".last_event_id]\n"
                'type = "uuid_val"\n'
                "\n"
                "[tables.event_delivery_cursor.columns"
                ".updated_at]\n"
                'type = "timestamp"\n'
            )

        if toml_file.name == "dispatch.toml":
            # Delta 3: Add config jsonb column to sources.
            insertion_point = (
                "[tables.sources.columns.created_at]"
            )
            column_addition = (
                "[tables.sources.columns.config]\n"
                'type = "json"\n'
                'default = "{}"\n'
                'comment = "Per-source mapping configuration"\n'
                "\n"
            )
            content = content.replace(
                insertion_point,
                column_addition + insertion_point,
            )

        if toml_file.name == "auth.toml":
            # Delta 4: Add hmac to credential_type enum.
            content = content.replace(
                'values = ["api_key", "bearer"]',
                'values = ["api_key", "bearer", "hmac"]',
            )

        dest.write_text(content)

    # Write pgdesign.toml pointing to the schema files.
    (schema_copy / "pgdesign.toml").write_text(
        '[project]\n'
        'name = "orxtra"\n'
        'version = "0.1.0"\n'
        'schemas = ["trace.toml", "dispatch.toml", "auth.toml"]\n',
    )

    return schema_copy


def _run_pgdesign(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run pgdesign as a subprocess and return the result."""
    return subprocess.run(  # noqa: S603
        [sys.executable, "-m", "pgdesign", *args],
        capture_output=True,
        text=True,
        check=False,
    )


async def test_synthetic_migration_four_categories(
    pg_container: Any,  # noqa: ANN401
) -> None:
    """Apply a migration covering all four 4.1 change categories."""
    import asyncpg as _asyncpg  # noqa: PLC0415
    from _generated.schema_executor import (  # noqa: PLC0415
        execute,
    )

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )

    conn = await _asyncpg.connect(url)
    try:
        # 1. Create baseline schema.
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")

        adapter = _AsyncpgAdapter(conn)
        result = await execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
        )
        assert not result.errors, (
            f"Baseline schema errors: {result.errors}"
        )

        # Verify baseline lacks all four deltas.
        tables = await conn.fetch(
            "SELECT table_name "
            "FROM information_schema.tables "
            "WHERE table_schema = 'public' "
            "AND table_name = 'event_delivery_cursor'",
        )
        assert len(tables) == 0

        cols = await conn.fetch(
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_name = 'events' "
            "AND column_name = 'idempotency_key'",
        )
        assert len(cols) == 0

        source_cols = await conn.fetch(
            "SELECT column_name "
            "FROM information_schema.columns "
            "WHERE table_name = 'sources' "
            "AND column_name = 'config'",
        )
        assert len(source_cols) == 0

        enum_vals = await conn.fetch(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = 'credential_type' "
            "AND e.enumlabel = 'hmac'",
        )
        assert len(enum_vals) == 0
    finally:
        await conn.close()

    # 2. Create modified TOML in a temp directory.
    with tempfile.TemporaryDirectory() as tmpdir:
        schema_copy = _create_modified_schema(Path(tmpdir))
        migrations_dir = Path(tmpdir) / "migrations"
        migrations_dir.mkdir()

        # 3. Generate migration.
        gen_result = _run_pgdesign([
            "migrate", "generate",
            str(schema_copy),
            "--db", url,
            "--version", "0.0.1-test",
            "--dir", str(migrations_dir),
            "--config", str(schema_copy / "pgdesign.toml"),
        ])
        assert gen_result.returncode == 0, (
            f"generate failed: {gen_result.stderr}"
        )

        # Verify migration files were generated.
        migration_files = list(migrations_dir.glob("*/up.sql"))
        assert len(migration_files) > 0, (
            f"No migration files. stderr: {gen_result.stderr}"
        )

        # 4. Apply migration.
        apply_result = _run_pgdesign([
            "migrate", "apply",
            "--db", url,
            "--dir", str(migrations_dir),
            "--no-dry-run",
        ])
        assert apply_result.returncode == 0, (
            f"apply failed: {apply_result.stderr}"
        )

        # 5. Verify all four changes took effect.
        conn2 = await _asyncpg.connect(url)
        try:
            # Category 1: New table exists.
            tables = await conn2.fetch(
                "SELECT table_name "
                "FROM information_schema.tables "
                "WHERE table_schema = 'public' "
                "AND table_name = 'event_delivery_cursor'",
            )
            assert len(tables) == 1, (
                "event_delivery_cursor should exist"
            )

            # Category 2: Column + partial unique index.
            cols = await conn2.fetch(
                "SELECT column_name "
                "FROM information_schema.columns "
                "WHERE table_name = 'events' "
                "AND column_name = 'idempotency_key'",
            )
            assert len(cols) == 1, (
                "idempotency_key column should exist"
            )

            idx = await conn2.fetch(
                "SELECT indexname FROM pg_indexes "
                "WHERE indexname = "
                "'idx_events_idempotency_key'",
            )
            assert len(idx) == 1, (
                "idx_events_idempotency_key should exist"
            )

            # Category 3: JSONB column on sources.
            source_cols = await conn2.fetch(
                "SELECT column_name "
                "FROM information_schema.columns "
                "WHERE table_name = 'sources' "
                "AND column_name = 'config'",
            )
            assert len(source_cols) == 1, (
                "config column should exist on sources"
            )

            # Category 4: Enum ADD VALUE.
            enum_vals = await conn2.fetch(
                "SELECT enumlabel FROM pg_enum e "
                "JOIN pg_type t ON e.enumtypid = t.oid "
                "WHERE t.typname = 'credential_type' "
                "AND e.enumlabel = 'hmac'",
            )
            assert len(enum_vals) == 1, (
                "hmac should exist in credential_type"
            )

            # 6. Verify migration status.
            status_result = _run_pgdesign([
                "migrate", "status",
                "--db", url,
                "--dir", str(migrations_dir),
            ])
            assert status_result.returncode == 0
            assert "0.0.1-test" in status_result.stdout
        finally:
            await conn2.close()
