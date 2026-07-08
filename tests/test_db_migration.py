"""Migration test harness for the schema evolution workflow.

Tests that the four schema changes introduced in 4.1 can be applied as a
migration against a live baseline database without data loss. Covers:

1. New table (dispatch_cursor, dispatch_completions)
2. New column + partial unique index (events.idempotency_key)
3. JSONB column (sources.config)
4. Enum ADD VALUE (credential_type += hmac) + new column (credentials.secret_ref)

The harness initializes from the committed baseline (tests/migration_baselines/v0.8.0/),
seeds test data, applies migration SQL, then asserts data preservation and schema
correctness.

Also includes existing tests for pgdesign migrate generate/status functionality.

Requires docker (testcontainers) and pgdesign binary. Skipped otherwise.
"""
from __future__ import annotations

import importlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

import pytest

from tests.pg_fixtures import skip_no_docker

pytestmark = skip_no_docker

_REPO_ROOT = Path(__file__).resolve().parents[1]
_SCHEMA_DIR = _REPO_ROOT / "schema"
_BASELINE_DIR = _REPO_ROOT / "tests" / "migration_baselines" / "v0.8.0"

# PG_UUIDV7_STUB: gen_random_uuid() stand-in for test containers.
_PG_UUIDV7_STUB = """\
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
    SELECT gen_random_uuid();
$$ LANGUAGE sql;
"""

# Migration SQL: the four delta changes from v0.8.0 to the current schema.
# These are the exact DDL statements that a migration would contain.
# Order matters: enum changes first, then tables, then columns + indexes.
_MIGRATION_SQL = [
    # 1. Enum ADD VALUE: credential_type += hmac
    # (must run outside a transaction in PG < 14, but inside is fine for PG >= 12
    # when there's no concurrent use of the enum)
    "ALTER TYPE credential_type ADD VALUE IF NOT EXISTS 'hmac'",

    # 2. New column: events.idempotency_key (nullable text)
    """ALTER TABLE events
       ADD COLUMN IF NOT EXISTS idempotency_key text""",

    # 3. Partial unique index on events.idempotency_key
    """CREATE UNIQUE INDEX IF NOT EXISTS idx_events_idempotency_key
       ON events (idempotency_key)
       WHERE idempotency_key IS NOT NULL""",

    # 4. New column: sources.config (nullable jsonb)
    """ALTER TABLE sources
       ADD COLUMN IF NOT EXISTS config jsonb""",

    # 5. GIN index on sources.config
    """CREATE INDEX IF NOT EXISTS idx_sources_config_gin
       ON sources USING gin (config)""",

    # 6. New column: credentials.secret_ref (nullable text)
    """ALTER TABLE credentials
       ADD COLUMN IF NOT EXISTS secret_ref text""",

    # 7. New table: dispatch_cursor
    """CREATE TABLE IF NOT EXISTS dispatch_cursor (
        id uuid NOT NULL DEFAULT uuid_generate_v7(),
        cursor_name text NOT NULL DEFAULT 'main',
        last_processed_event_id uuid NOT NULL,
        last_processed_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_dispatch_cursor PRIMARY KEY (id),
        CONSTRAINT chk_dispatch_cursor_name_not_empty CHECK (cursor_name <> ''),
        CONSTRAINT uq_dispatch_cursor_name UNIQUE (cursor_name),
        CONSTRAINT fk_dispatch_cursor_event
            FOREIGN KEY (last_processed_event_id) REFERENCES events (id) ON DELETE RESTRICT
    )""",

    # 8. Index on dispatch_cursor.last_processed_event_id
    """CREATE INDEX IF NOT EXISTS idx_dispatch_cursor_event
       ON dispatch_cursor (last_processed_event_id)""",

    # 9. New table: dispatch_completions
    """CREATE TABLE IF NOT EXISTS dispatch_completions (
        id uuid NOT NULL DEFAULT uuid_generate_v7(),
        event_id uuid NOT NULL,
        subscription_action_id uuid NOT NULL,
        result_status text NOT NULL,
        completed_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_dispatch_completions PRIMARY KEY (id),
        CONSTRAINT chk_dispatch_completions_status_valid
            CHECK (result_status IN ('success', 'error', 'skipped')),
        CONSTRAINT uq_dispatch_completions_event_action
            UNIQUE (event_id, subscription_action_id),
        CONSTRAINT fk_dispatch_completions_event
            FOREIGN KEY (event_id) REFERENCES events (id) ON DELETE RESTRICT,
        CONSTRAINT fk_dispatch_completions_action
            FOREIGN KEY (subscription_action_id) REFERENCES subscription_actions (id) ON DELETE CASCADE
    )""",

    # 10. Indexes on dispatch_completions
    """CREATE INDEX IF NOT EXISTS idx_dispatch_completions_event
       ON dispatch_completions (event_id)""",

    """CREATE INDEX IF NOT EXISTS idx_dispatch_completions_action
       ON dispatch_completions (subscription_action_id)""",
]


def _load_baseline_executor() -> Any:
    """Import the schema_executor from the frozen v0.8.0 baseline.

    The baseline directory contains a copy of the _generated package from
    the v0.8.0 schema. We register it as a unique package name
    (_baseline_generated) to avoid collisions with the live _generated
    package on sys.path.
    """
    _pkg = "_baseline_generated"

    # If already loaded (e.g., multiple tests), return cached module
    executor_key = f"{_pkg}.schema_executor"
    if executor_key in sys.modules:
        return sys.modules[executor_key]

    baseline_str = str(_BASELINE_DIR)

    # Register the baseline directory as a package
    pkg_spec = importlib.util.spec_from_file_location(
        _pkg,
        _BASELINE_DIR / "__init__.py",
        submodule_search_locations=[baseline_str],
    )
    if pkg_spec is None or pkg_spec.loader is None:
        msg = "Cannot load baseline __init__.py"
        raise ImportError(msg)
    pkg_mod = importlib.util.module_from_spec(pkg_spec)
    sys.modules[_pkg] = pkg_mod
    pkg_spec.loader.exec_module(pkg_mod)

    # Load each sub-module the executor imports from its package
    for sub_name in (
        "extensions",
        "types",
        "tables_trace",
        "tables_dispatch",
        "tables_auth",
        "post_tables",
    ):
        sub_spec = importlib.util.spec_from_file_location(
            f"{_pkg}.{sub_name}",
            _BASELINE_DIR / f"{sub_name}.py",
            submodule_search_locations=[],
        )
        if sub_spec is None or sub_spec.loader is None:
            msg = f"Cannot load baseline {sub_name}.py"
            raise ImportError(msg)
        sub_mod = importlib.util.module_from_spec(sub_spec)
        sub_mod.__package__ = _pkg
        sys.modules[f"{_pkg}.{sub_name}"] = sub_mod
        setattr(pkg_mod, sub_name, sub_mod)
        sub_spec.loader.exec_module(sub_mod)

    # Load the executor module itself. Its relative imports (from .extensions, etc.)
    # need to resolve against _baseline_generated.*, so we set __package__.
    exec_spec = importlib.util.spec_from_file_location(
        executor_key,
        _BASELINE_DIR / "schema_executor.py",
        submodule_search_locations=[],
    )
    if exec_spec is None or exec_spec.loader is None:
        msg = "Cannot load baseline schema_executor.py"
        raise ImportError(msg)
    exec_mod = importlib.util.module_from_spec(exec_spec)
    exec_mod.__package__ = _pkg
    sys.modules[executor_key] = exec_mod
    exec_spec.loader.exec_module(exec_mod)

    return exec_mod


from orxtra.services import AsyncpgAdapter

# ---------------------------------------------------------------------------
# Test: full baseline -> migrate -> verify cycle
# ---------------------------------------------------------------------------


async def test_migration_from_v080_baseline(
    pg_container: Any,
) -> None:
    """Full migration test: baseline v0.8.0 -> current schema.

    1. Initialize DB from frozen v0.8.0 baseline
    2. Seed test data (run, event, source, consumer, credential)
    3. Apply migration SQL (the four schema changes)
    4. Assert: data preserved, new columns/tables/types present
    """
    import asyncpg as _asyncpg

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )

    # --- Step 1: Apply baseline schema ---
    baseline_executor = _load_baseline_executor()

    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
        await conn.execute(_PG_UUIDV7_STUB)

        adapter = AsyncpgAdapter(conn)
        result = await baseline_executor.execute(
            adapter,
            idempotent=True,
            extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
        )
        if result.errors:
            err_msg = "; ".join(
                f"{kind}.{name}: {err}"
                for kind, name, err in result.errors
            )
            msg = f"Baseline schema creation failed: {err_msg}"
            raise RuntimeError(msg)

        # Verify baseline tables exist
        tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name",
        )
        table_names = [r["table_name"] for r in tables]
        assert "runs" in table_names, f"Baseline schema missing 'runs'. Tables: {table_names}"
        assert "events" in table_names, f"Baseline schema missing 'events'. Tables: {table_names}"
        assert "sources" in table_names, f"Baseline schema missing 'sources'. Tables: {table_names}"
        assert "credentials" in table_names, f"Baseline schema missing 'credentials'. Tables: {table_names}"

        # Verify baseline does NOT have the new objects
        assert "dispatch_cursor" not in table_names, "dispatch_cursor should not exist in baseline"
        assert "dispatch_completions" not in table_names, "dispatch_completions should not exist in baseline"

        events_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'events' ORDER BY ordinal_position",
        )
        events_col_names = [c["column_name"] for c in events_cols]
        assert "idempotency_key" not in events_col_names, (
            "idempotency_key should not exist in baseline events"
        )

        sources_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'sources' ORDER BY ordinal_position",
        )
        sources_col_names = [c["column_name"] for c in sources_cols]
        assert "config" not in sources_col_names, (
            "config should not exist in baseline sources"
        )

        creds_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'credentials' ORDER BY ordinal_position",
        )
        creds_col_names = [c["column_name"] for c in creds_cols]
        assert "secret_ref" not in creds_col_names, (
            "secret_ref should not exist in baseline credentials"
        )

        baseline_enum = await conn.fetch(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = 'credential_type' ORDER BY e.enumsortorder",
        )
        baseline_labels = [r["enumlabel"] for r in baseline_enum]
        assert "hmac" not in baseline_labels, "hmac should not exist in baseline enum"

        # --- Step 2: Seed test data ---
        run_id = await conn.fetchval(
            """INSERT INTO runs (intent, autonomy_level)
               VALUES ($1, $2)
               RETURNING id""",
            "test migration intent",
            "medium",
        )
        event_id = await conn.fetchval(
            """INSERT INTO events (run_id, event_type, data)
               VALUES ($1, $2, $3::jsonb)
               RETURNING id""",
            run_id,
            "test.migration_event",
            '{"key": "value"}',
        )
        source_id = await conn.fetchval(
            """INSERT INTO sources (slug, name)
               VALUES ($1, $2)
               RETURNING id""",
            "test-source",
            "Test Source",
        )
        consumer_id = await conn.fetchval(
            """INSERT INTO consumers (name, trust_tier)
               VALUES ($1, $2)
               RETURNING id""",
            "test-consumer",
            "identified",
        )
        credential_id = await conn.fetchval(
            """INSERT INTO credentials (consumer_id, credential_type, credential_hash)
               VALUES ($1, $2, $3)
               RETURNING id""",
            consumer_id,
            "api_key",
            "sha256_hash_placeholder",
        )

        # Also seed a subscription + action (needed for dispatch_completions FK)
        sub_id = await conn.fetchval(
            """INSERT INTO subscriptions (storage)
               VALUES ('persistent')
               RETURNING id""",
        )
        action_id = await conn.fetchval(
            """INSERT INTO subscription_actions (subscription_id, position, action_type)
               VALUES ($1, 0, 'log')
               RETURNING id""",
            sub_id,
        )

        # --- Step 3: Apply migration SQL ---
        for stmt in _MIGRATION_SQL:
            await conn.execute(stmt)

        # --- Step 4: Verify data preservation and schema correctness ---

        # 4a: Seeded data is preserved
        run_row = await conn.fetchrow("SELECT * FROM runs WHERE id = $1", run_id)
        assert run_row is not None
        assert run_row["intent"] == "test migration intent"

        event_row = await conn.fetchrow("SELECT * FROM events WHERE id = $1", event_id)
        assert event_row is not None
        assert event_row["event_type"] == "test.migration_event"

        source_row = await conn.fetchrow("SELECT * FROM sources WHERE id = $1", source_id)
        assert source_row is not None
        assert source_row["slug"] == "test-source"

        credential_row = await conn.fetchrow(
            "SELECT * FROM credentials WHERE id = $1", credential_id,
        )
        assert credential_row is not None
        assert credential_row["credential_type"] == "api_key"
        assert credential_row["credential_hash"] == "sha256_hash_placeholder"

        consumer_row = await conn.fetchrow(
            "SELECT * FROM consumers WHERE id = $1", consumer_id,
        )
        assert consumer_row is not None
        assert consumer_row["name"] == "test-consumer"

        # 4b: New column - events.idempotency_key exists and is NULL for old rows
        assert event_row["idempotency_key"] is None, (
            "Pre-existing events should have NULL idempotency_key"
        )

        # Verify partial unique index: duplicate key -> conflict
        await conn.execute(
            """INSERT INTO events (run_id, event_type, idempotency_key)
               VALUES ($1, 'test.dedup1', 'unique-key-1')""",
            run_id,
        )
        with pytest.raises(Exception, match="idx_events_idempotency_key"):
            await conn.execute(
                """INSERT INTO events (run_id, event_type, idempotency_key)
                   VALUES ($1, 'test.dedup2', 'unique-key-1')""",
                run_id,
            )
        # NULL keys should not conflict (partial index excludes NULLs)
        await conn.execute(
            """INSERT INTO events (run_id, event_type)
               VALUES ($1, 'test.null_key1')""",
            run_id,
        )
        await conn.execute(
            """INSERT INTO events (run_id, event_type)
               VALUES ($1, 'test.null_key2')""",
            run_id,
        )

        # 4c: New column - sources.config exists and is NULL for old rows
        assert source_row["config"] is None, (
            "Pre-existing sources should have NULL config"
        )
        # Verify config can hold JSONB data
        await conn.execute(
            "UPDATE sources SET config = $1::jsonb WHERE id = $2",
            '{"event_type_field": "type"}',
            source_id,
        )
        updated_source = await conn.fetchrow(
            "SELECT config FROM sources WHERE id = $1", source_id,
        )
        assert updated_source is not None
        assert updated_source["config"] is not None

        # 4d: New tables - dispatch_cursor and dispatch_completions exist
        post_migration_tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public' ORDER BY table_name",
        )
        post_table_names = [r["table_name"] for r in post_migration_tables]
        assert "dispatch_cursor" in post_table_names, "dispatch_cursor should exist"
        assert "dispatch_completions" in post_table_names, "dispatch_completions should exist"

        # Verify dispatch_cursor columns
        cursor_cols = await conn.fetch(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'dispatch_cursor' ORDER BY ordinal_position",
        )
        cursor_col_names = [c["column_name"] for c in cursor_cols]
        assert "id" in cursor_col_names
        assert "cursor_name" in cursor_col_names
        assert "last_processed_event_id" in cursor_col_names
        assert "last_processed_at" in cursor_col_names

        # Verify dispatch_completions columns
        comp_cols = await conn.fetch(
            "SELECT column_name, data_type, is_nullable "
            "FROM information_schema.columns "
            "WHERE table_name = 'dispatch_completions' ORDER BY ordinal_position",
        )
        comp_col_names = [c["column_name"] for c in comp_cols]
        assert "id" in comp_col_names
        assert "event_id" in comp_col_names
        assert "subscription_action_id" in comp_col_names
        assert "result_status" in comp_col_names
        assert "completed_at" in comp_col_names

        # Verify dispatch_cursor can be inserted into and FK works
        await conn.execute(
            """INSERT INTO dispatch_cursor (cursor_name, last_processed_event_id, last_processed_at)
               VALUES ($1, $2, now())""",
            "main",
            event_id,
        )
        cursor_row = await conn.fetchrow(
            "SELECT * FROM dispatch_cursor WHERE cursor_name = 'main'",
        )
        assert cursor_row is not None
        assert cursor_row["last_processed_event_id"] == event_id

        # Verify dispatch_completions can be inserted into
        await conn.execute(
            """INSERT INTO dispatch_completions (event_id, subscription_action_id, result_status)
               VALUES ($1, $2, 'success')""",
            event_id,
            action_id,
        )
        comp_row = await conn.fetchrow(
            "SELECT * FROM dispatch_completions WHERE event_id = $1",
            event_id,
        )
        assert comp_row is not None
        assert comp_row["result_status"] == "success"

        # Verify unique constraint on (event_id, subscription_action_id)
        with pytest.raises(Exception, match="uq_dispatch_completions_event_action"):
            await conn.execute(
                """INSERT INTO dispatch_completions (event_id, subscription_action_id, result_status)
                   VALUES ($1, $2, 'error')""",
                event_id,
                action_id,
            )

        # Verify result_status check constraint
        with pytest.raises(Exception, match="chk_dispatch_completions_status_valid"):
            await conn.execute(
                """INSERT INTO dispatch_completions (event_id, subscription_action_id, result_status)
                   VALUES ($1, $2, 'invalid')""",
                event_id,
                action_id,
            )

        # 4e: Enum ADD VALUE - credential_type now includes 'hmac'
        enum_values = await conn.fetch(
            "SELECT enumlabel FROM pg_enum e "
            "JOIN pg_type t ON e.enumtypid = t.oid "
            "WHERE t.typname = 'credential_type' ORDER BY e.enumsortorder",
        )
        enum_labels = [r["enumlabel"] for r in enum_values]
        assert enum_labels == ["api_key", "bearer", "hmac"], (
            f"credential_type enum should be [api_key, bearer, hmac], got: {enum_labels}"
        )

        # 4f: New column - credentials.secret_ref exists and is NULL for old rows
        assert credential_row["secret_ref"] is None, (
            "Pre-existing credentials should have NULL secret_ref"
        )

        # Verify hmac credential with secret_ref can be inserted
        await conn.execute(
            """INSERT INTO credentials (consumer_id, credential_type, credential_hash, secret_ref)
               VALUES ($1, 'hmac', 'hmac_hash_placeholder', 'env:WEBHOOK_SECRET')""",
            consumer_id,
        )
        hmac_cred = await conn.fetchrow(
            "SELECT * FROM credentials "
            "WHERE credential_type = 'hmac' AND consumer_id = $1",
            consumer_id,
        )
        assert hmac_cred is not None
        assert hmac_cred["secret_ref"] == "env:WEBHOOK_SECRET"

    finally:
        await conn.close()


# ---------------------------------------------------------------------------
# Existing tests for pgdesign migrate generate/status
# ---------------------------------------------------------------------------


_PGDESIGN_TOML = """\
[project]
name = "orxtra"
version = "0.1.0"
schemas = ["trace.toml", "dispatch.toml", "auth.toml"]
"""


def _pgdesign_bin() -> str:
    """Locate the pgdesign binary."""
    path = shutil.which("pgdesign")
    if path is None:
        msg = "pgdesign binary not found on PATH"
        raise FileNotFoundError(msg)
    return path


def _run_pgdesign(args: list[str]) -> subprocess.CompletedProcess[str]:
    """Run pgdesign as a subprocess and return the result."""
    return subprocess.run(  # noqa: S603
        [_pgdesign_bin(), *args],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(
    shutil.which("pgdesign") is None,
    reason="pgdesign binary not found on PATH",
)
async def test_pgdesign_migrate_generate_succeeds(
    pg_container: Any,
) -> None:
    """pgdesign can generate a baseline migration from the schema."""
    import asyncpg as _asyncpg

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )

    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
    finally:
        await conn.close()

    with tempfile.TemporaryDirectory() as tmpdir:
        tmppath = Path(tmpdir)
        schema_dir = tmppath / "schema"
        schema_dir.mkdir()
        migrations_dir = tmppath / "migrations"
        migrations_dir.mkdir()

        for toml_file in _SCHEMA_DIR.glob("*.toml"):
            shutil.copy2(toml_file, schema_dir / toml_file.name)
        (schema_dir / "pgdesign.toml").write_text(_PGDESIGN_TOML)

        gen = _run_pgdesign([
            "migrate", "generate",
            str(schema_dir),
            "--db", url,
            "--version", "0.1.0",
            "--dir", str(migrations_dir),
            "--config", str(schema_dir / "pgdesign.toml"),
        ])
        assert gen.returncode == 0, (
            f"generate failed: {gen.stderr}"
        )

        migration_files = list(migrations_dir.glob("*.toml"))
        assert len(migration_files) == 1
        assert migration_files[0].name == "0.1.0.toml"

        # The generated migration should reference key tables.
        content = migration_files[0].read_text()
        assert "runs" in content
        assert "events" in content
        assert "subscriptions" in content


@pytest.mark.skipif(
    shutil.which("pgdesign") is None,
    reason="pgdesign binary not found on PATH",
)
async def test_pgdesign_migrate_status_on_empty_db(
    pg_container: Any,
) -> None:
    """migrate status works on a DB with no migration history."""
    import asyncpg as _asyncpg

    url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )

    conn = await _asyncpg.connect(url)
    try:
        await conn.execute("DROP SCHEMA public CASCADE")
        await conn.execute("CREATE SCHEMA public")
    finally:
        await conn.close()

    with tempfile.TemporaryDirectory() as tmpdir:
        migrations_dir = Path(tmpdir) / "migrations"
        migrations_dir.mkdir()

        status = _run_pgdesign([
            "migrate", "status",
            "--db", url,
            "--dir", str(migrations_dir),
        ])
        # Status should succeed (no migrations to show).
        assert status.returncode == 0
