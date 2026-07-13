"""Migration test harness for the schema evolution workflow.

Tests that the full v0.8.0 -> current delta can be applied as a single
forward migration against a live baseline database without data loss. The
migration is reviewed here as ONE artifact -- ordering matters end to end:
the principals table and its unique constraint precede every FK into it;
each backfill precedes the NOT NULL that depends on it; the events
append-only trigger is disabled only for the bracketed backfill and
re-enabled immediately after. ``_MIGRATION_SQL`` is the ordered statement
list; the assertions below verify both data preservation and schema shape.

Coverage, grouped by vertical:

1. Dispatch/dedup deltas: dispatch_cursor, dispatch_completions,
   events.idempotency_key (+ partial unique index), sources.config (+ GIN),
   credential_type += hmac, credentials.secret_ref.
2. Identity table: principals (+ unique (kind, external_ref)) with the
   singleton system principal seeded.
3. Identity at birth: runs.created_by, sources.created_by,
   consumers.principal_id -- each minted a principal, backfilled, then
   pinned NOT NULL + FK (runs/sources/consumers are RESTRICT).
4. Events attribution flip: events.source -> events.principal_id, backfilled
   across all four legacy shapes (run-attached, overseer-with-run,
   run-less worker, slug-matched webhook) plus an orphaned slug, then
   NOT NULL + FK, old column/index/NOTIFY body dropped.
5. Inbox resolution: inbox_items.resolved_by (nullable, no backfill).
6. Subscription ownership cutover: owner_run_id -> principal_id (CASCADE),
   historical rows attributed to the system principal.

The harness initializes from the committed baseline
(tests/migration_baselines/v0.8.0/), seeds every legacy shape, applies the
migration SQL, then asserts data preservation and schema correctness.

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

# Migration SQL: the 47 ordered DDL statements from the v0.8.0 baseline to the
# current schema. These are the exact statements a migration would contain.
# Order follows the dependency chain: the credential_type enum ADD VALUE and
# the events/sources/credentials/dispatch additions first, then the full
# identity migration -- create the principals table, then for each attributing
# table (runs, sources, consumers, events, subscriptions) mint principals ->
# add the nullable FK column -> backfill -> SET NOT NULL -> add the FK ->
# index, plus the inbox_items.resolved_by column (nullable, no backfill) and
# the NOTIFY trigger swap (source -> principal_id) -- and finally dropping the
# superseded columns (events.source, subscriptions.owner_run_id).
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

    # 11. New table: principals (identity module; absent in the v0.8.0 baseline).
    """CREATE TABLE IF NOT EXISTS principals (
        id uuid NOT NULL DEFAULT uuid_generate_v7(),
        kind text NOT NULL,
        external_ref uuid NOT NULL,
        display_name text,
        created_at timestamptz NOT NULL DEFAULT now(),
        CONSTRAINT pk_principals PRIMARY KEY (id)
    )""",

    # 12. Unique (kind, external_ref) so mint + backfill stay idempotent.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_principals_kind_external_ref'
        AND conrelid = 'principals'::regclass
      ) THEN
        ALTER TABLE principals
            ADD CONSTRAINT uq_principals_kind_external_ref
            UNIQUE (kind, external_ref);
      END IF;
    END $$;""",

    # 13. Seed the singleton system principal (all-zeros external_ref sentinel).
    """INSERT INTO principals (kind, external_ref, display_name)
       VALUES ('system', '00000000-0000-0000-0000-000000000000', 'system')
       ON CONFLICT (kind, external_ref) DO NOTHING""",

    # 14. Mint a run principal per existing run (kind=run, external_ref=run id,
    # display_name NULL -- the runs table carries the run's descriptive data).
    """INSERT INTO principals (kind, external_ref, display_name)
       SELECT 'run', r.id, NULL FROM runs r
       ON CONFLICT (kind, external_ref) DO NOTHING""",

    # 15. New column: runs.created_by (nullable first so existing rows survive).
    """ALTER TABLE runs ADD COLUMN IF NOT EXISTS created_by uuid""",

    # 16. Backfill ALL existing runs to the system principal (the original
    # creator is unknowable for historical rows).
    """UPDATE runs SET created_by = (
           SELECT id FROM principals
           WHERE kind = 'system'
           AND external_ref = '00000000-0000-0000-0000-000000000000'
       ) WHERE created_by IS NULL""",

    # 17. Enforce NOT NULL now that every row is backfilled.
    """ALTER TABLE runs ALTER COLUMN created_by SET NOT NULL""",

    # 18. FK runs.created_by -> principals.id ON DELETE RESTRICT.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_runs_created_by'
        AND conrelid = 'runs'::regclass
      ) THEN
        ALTER TABLE runs ADD CONSTRAINT fk_runs_created_by
            FOREIGN KEY (created_by) REFERENCES principals (id)
            ON DELETE RESTRICT;
      END IF;
    END $$;""",

    # 19. Index on runs.created_by.
    """CREATE INDEX IF NOT EXISTS idx_runs_created_by
       ON runs (created_by)""",

    # 20. Mint a source principal per existing source (kind=source,
    # external_ref=source id, display_name=slug -- the source's own principal
    # carries the slug as its label).
    """INSERT INTO principals (kind, external_ref, display_name)
       SELECT 'source', s.id, s.slug FROM sources s
       ON CONFLICT (kind, external_ref) DO NOTHING""",

    # 21. New column: sources.created_by (nullable first so existing rows
    # survive).
    """ALTER TABLE sources ADD COLUMN IF NOT EXISTS created_by uuid""",

    # 22. Backfill ALL existing sources to the system principal (the original
    # creator is unknowable for historical rows).
    """UPDATE sources SET created_by = (
           SELECT id FROM principals
           WHERE kind = 'system'
           AND external_ref = '00000000-0000-0000-0000-000000000000'
       ) WHERE created_by IS NULL""",

    # 23. Enforce NOT NULL now that every source row is backfilled.
    """ALTER TABLE sources ALTER COLUMN created_by SET NOT NULL""",

    # 24. FK sources.created_by -> principals.id ON DELETE RESTRICT.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_sources_created_by'
        AND conrelid = 'sources'::regclass
      ) THEN
        ALTER TABLE sources ADD CONSTRAINT fk_sources_created_by
            FOREIGN KEY (created_by) REFERENCES principals (id)
            ON DELETE RESTRICT;
      END IF;
    END $$;""",

    # 25. Index on sources.created_by.
    """CREATE INDEX IF NOT EXISTS idx_sources_created_by
       ON sources (created_by)""",

    # 26. Mint a consumer principal per existing consumer (kind=consumer,
    # external_ref=consumer id, display_name=name -- each consumer is backed
    # by its OWN identity row, not the system principal).
    """INSERT INTO principals (kind, external_ref, display_name)
       SELECT 'consumer', c.id, c.name FROM consumers c
       ON CONFLICT (kind, external_ref) DO NOTHING""",

    # 27. New column: consumers.principal_id (nullable first so existing rows
    # survive).
    """ALTER TABLE consumers ADD COLUMN IF NOT EXISTS principal_id uuid""",

    # 28. Backfill each consumer to ITS OWN principal (the one minted in 26),
    # matched by external_ref = consumer id.
    """UPDATE consumers c SET principal_id = (
           SELECT p.id FROM principals p
           WHERE p.kind = 'consumer' AND p.external_ref = c.id
       ) WHERE c.principal_id IS NULL""",

    # 29. Enforce NOT NULL now that every consumer is backfilled.
    """ALTER TABLE consumers ALTER COLUMN principal_id SET NOT NULL""",

    # 30. FK consumers.principal_id -> principals.id ON DELETE RESTRICT.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_consumers_principal'
        AND conrelid = 'consumers'::regclass
      ) THEN
        ALTER TABLE consumers ADD CONSTRAINT fk_consumers_principal
            FOREIGN KEY (principal_id) REFERENCES principals (id)
            ON DELETE RESTRICT;
      END IF;
    END $$;""",

    # 31. UNIQUE (principal_id): a consumer has exactly one identity row.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'uq_consumers_principal_id'
        AND conrelid = 'consumers'::regclass
      ) THEN
        ALTER TABLE consumers ADD CONSTRAINT uq_consumers_principal_id
            UNIQUE (principal_id);
      END IF;
    END $$;""",

    # -- events attribution flip: events.source -> events.principal_id --------

    # 32. New column: events.principal_id (nullable first so existing rows
    # survive the backfill).
    """ALTER TABLE events ADD COLUMN IF NOT EXISTS principal_id uuid""",

    # 33. events is append-only (deny_mutation trigger blocks UPDATE/DELETE).
    # Disable it for the one-time backfill, then re-enable in 33d.
    """ALTER TABLE events DISABLE TRIGGER deny_mutation""",

    # 33a. Run-attached events -> that run's principal (kind='run',
    # external_ref = the run id). Covers both 'internal' and 'overseer' sources
    # that carry a run_id.
    """UPDATE events e SET principal_id = p.id
       FROM principals p
       WHERE e.run_id IS NOT NULL
         AND p.kind = 'run' AND p.external_ref = e.run_id
         AND e.principal_id IS NULL""",

    # 33b. Run-less events whose source matches an existing source slug -> that
    # source's principal (kind='source', external_ref = the source id).
    """UPDATE events e SET principal_id = p.id
       FROM sources s
       JOIN principals p ON p.kind = 'source' AND p.external_ref = s.id
       WHERE e.run_id IS NULL AND e.source = s.slug
         AND e.principal_id IS NULL""",

    # 33c. Everything else (internal without a run, dispatch-worker, orphaned
    # slugs whose source row no longer exists) -> the system principal.
    """UPDATE events SET principal_id = (
           SELECT id FROM principals
           WHERE kind = 'system'
           AND external_ref = '00000000-0000-0000-0000-000000000000'
       ) WHERE principal_id IS NULL""",

    # 33d. Re-enable the append-only trigger now that the backfill is done.
    """ALTER TABLE events ENABLE TRIGGER deny_mutation""",

    # 34. Enforce NOT NULL now that every event has an actor.
    """ALTER TABLE events ALTER COLUMN principal_id SET NOT NULL""",

    # 35. FK events.principal_id -> principals.id ON DELETE RESTRICT.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_events_principal'
        AND conrelid = 'events'::regclass
      ) THEN
        ALTER TABLE events ADD CONSTRAINT fk_events_principal
            FOREIGN KEY (principal_id) REFERENCES principals (id)
            ON DELETE RESTRICT;
      END IF;
    END $$;""",

    # 36. New index on (principal_id, created_at DESC).
    """CREATE INDEX IF NOT EXISTS idx_events_principal_created
       ON events (principal_id, created_at DESC)""",

    # 37. Drop the old source column and its index.
    """DROP INDEX IF EXISTS idx_events_source_created""",
    """ALTER TABLE events DROP COLUMN IF EXISTS source""",

    # 38. Replace the NOTIFY trigger function body: source -> principal_id.
    """CREATE OR REPLACE FUNCTION notify_orxtra_event() RETURNS trigger AS $$
BEGIN
    PERFORM pg_notify('orxtra_events', json_build_object(
        'event_id', NEW.id,
        'run_id', NEW.run_id,
        'principal_id', NEW.principal_id::text,
        'event_type', NEW.event_type
    )::text);
    RETURN NEW;
END;
$$ LANGUAGE plpgsql""",

    # -- inbox resolution attribution: inbox_items.resolved_by --------------

    # 39. New column: inbox_items.resolved_by (nullable, NO backfill --
    # historical resolutions predate attribution and stay NULL as an honest
    # unknown; NULL also means "still pending").
    """ALTER TABLE inbox_items ADD COLUMN IF NOT EXISTS resolved_by uuid""",

    # 40. FK inbox_items.resolved_by -> principals.id ON DELETE RESTRICT.
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_inbox_resolved_by'
        AND conrelid = 'inbox_items'::regclass
      ) THEN
        ALTER TABLE inbox_items ADD CONSTRAINT fk_inbox_resolved_by
            FOREIGN KEY (resolved_by) REFERENCES principals (id)
            ON DELETE RESTRICT;
      END IF;
    END $$;""",

    # 41. Index on inbox_items.resolved_by (matches the runs/sources FK style).
    """CREATE INDEX IF NOT EXISTS idx_inbox_resolved_by
       ON inbox_items (resolved_by)""",

    # -- subscription ownership cutover: owner_run_id -> principal_id ---------

    # 42. New column: subscriptions.principal_id (nullable first so existing
    # rows survive the backfill).
    """ALTER TABLE subscriptions ADD COLUMN IF NOT EXISTS principal_id uuid""",

    # 43. Backfill ALL existing subscriptions to the system principal.
    # owner_run_id was never populated in production; even where a seeded row
    # carries one, ownership history is unknowable, so every historical
    # subscription is attributed to the system principal.
    """UPDATE subscriptions SET principal_id = (
           SELECT id FROM principals
           WHERE kind = 'system'
           AND external_ref = '00000000-0000-0000-0000-000000000000'
       ) WHERE principal_id IS NULL""",

    # 44. Enforce NOT NULL now that every subscription has an owner.
    """ALTER TABLE subscriptions ALTER COLUMN principal_id SET NOT NULL""",

    # 45. FK subscriptions.principal_id -> principals.id ON DELETE CASCADE.
    # CASCADE is deliberate: a subscription is operational state that dies with
    # its owner (contrast the RESTRICT history FKs).
    """DO $$
    BEGIN
      IF NOT EXISTS (
        SELECT 1 FROM pg_constraint
        WHERE conname = 'fk_subscriptions_principal'
        AND conrelid = 'subscriptions'::regclass
      ) THEN
        ALTER TABLE subscriptions ADD CONSTRAINT fk_subscriptions_principal
            FOREIGN KEY (principal_id) REFERENCES principals (id)
            ON DELETE CASCADE;
      END IF;
    END $$;""",

    # 46. Index on subscriptions.principal_id.
    """CREATE INDEX IF NOT EXISTS idx_subscriptions_principal_id
       ON subscriptions (principal_id)""",

    # 47. Drop the old owner_run_id column, its FK, and the baseline index.
    """DROP INDEX IF EXISTS idx_subscriptions_owner_run_id""",
    """ALTER TABLE subscriptions DROP CONSTRAINT IF EXISTS fk_subscriptions_run""",
    """ALTER TABLE subscriptions DROP COLUMN IF EXISTS owner_run_id""",
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
        assert "principals" not in table_names, "principals should not exist in baseline"

        runs_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'runs' ORDER BY ordinal_position",
        )
        runs_col_names = [c["column_name"] for c in runs_cols]
        assert "created_by" not in runs_col_names, (
            "created_by should not exist in baseline runs"
        )

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
        assert "created_by" not in sources_col_names, (
            "created_by should not exist in baseline sources"
        )

        consumers_cols = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'consumers' ORDER BY ordinal_position",
        )
        consumers_col_names = [c["column_name"] for c in consumers_cols]
        assert "principal_id" not in consumers_col_names, (
            "principal_id should not exist in baseline consumers"
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

        # Seed the remaining legacy event shapes so the principal_id backfill
        # is exercised across all four rules (plus an orphaned slug).
        # (1) event_id above is the run-attached 'internal' event.
        # (2) overseer-sourced event that still carries a run_id.
        event_overseer_id = await conn.fetchval(
            """INSERT INTO events (run_id, event_type, source, data)
               VALUES ($1, $2, $3, $4::jsonb) RETURNING id""",
            run_id, "run.overseer_note", "overseer", "{}",
        )
        # (3) dispatch-worker event with no run.
        event_worker_id = await conn.fetchval(
            """INSERT INTO events (run_id, event_type, source, data)
               VALUES (NULL, $1, $2, $3::jsonb) RETURNING id""",
            "dispatch.refired", "dispatch-worker", "{}",
        )
        # (4) webhook event whose source matches an existing source slug.
        event_webhook_id = await conn.fetchval(
            """INSERT INTO events (run_id, event_type, source, data)
               VALUES (NULL, $1, $2, $3::jsonb) RETURNING id""",
            "webhook.push", "test-source", "{}",
        )
        # (5) orphaned-slug event: its source row does not exist.
        event_orphan_id = await conn.fetchval(
            """INSERT INTO events (run_id, event_type, source, data)
               VALUES (NULL, $1, $2, $3::jsonb) RETURNING id""",
            "webhook.ghost", "ghost-slug-404", "{}",
        )

        # Seed two inbox items in the v0.8.0 baseline (no resolved_by column
        # yet): one already answered (a historical resolution) and one pending.
        # Both must end up with resolved_by NULL after migration -- historical
        # resolutions are deliberately NOT backfilled.
        inbox_answered_id = await conn.fetchval(
            """INSERT INTO inbox_items
               (run_id, decision_type, question, status, answer, answered_at)
               VALUES ($1, $2, $3, 'answered', 'yes', now())
               RETURNING id""",
            run_id, "approval", "historical answered?",
        )
        inbox_pending_id = await conn.fetchval(
            """INSERT INTO inbox_items (run_id, decision_type, question)
               VALUES ($1, $2, $3)
               RETURNING id""",
            run_id, "approval", "still pending?",
        )

        # Also seed a subscription + action (needed for dispatch_completions FK).
        # The v0.8.0 baseline has owner_run_id: set it so the cutover proves that
        # ownership history is discarded (backfilled to system, not the run).
        sub_id = await conn.fetchval(
            """INSERT INTO subscriptions (storage, owner_run_id)
               VALUES ('persistent', $1)
               RETURNING id""",
            run_id,
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

        # Post-migration inserts must supply principal_id (now NOT NULL). Use
        # the system principal as a valid actor for these index-behavior probes.
        sys_pid = await conn.fetchval(
            "SELECT id FROM principals WHERE kind = 'system'"
            " AND external_ref = '00000000-0000-0000-0000-000000000000'",
        )

        # Verify partial unique index: duplicate key -> conflict
        await conn.execute(
            """INSERT INTO events (run_id, event_type, idempotency_key, principal_id)
               VALUES ($1, 'test.dedup1', 'unique-key-1', $2)""",
            run_id, sys_pid,
        )
        with pytest.raises(Exception, match="idx_events_idempotency_key"):
            await conn.execute(
                """INSERT INTO events (run_id, event_type, idempotency_key, principal_id)
                   VALUES ($1, 'test.dedup2', 'unique-key-1', $2)""",
                run_id, sys_pid,
            )
        # NULL keys should not conflict (partial index excludes NULLs)
        await conn.execute(
            """INSERT INTO events (run_id, event_type, principal_id)
               VALUES ($1, 'test.null_key1', $2)""",
            run_id, sys_pid,
        )
        await conn.execute(
            """INSERT INTO events (run_id, event_type, principal_id)
               VALUES ($1, 'test.null_key2', $2)""",
            run_id, sys_pid,
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

        # 4g: principals table now exists with the system principal seeded.
        post_migration_tables = await conn.fetch(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_schema = 'public'",
        )
        assert "principals" in {
            r["table_name"] for r in post_migration_tables
        }, "principals should exist after migration"

        system_principal = await conn.fetchrow(
            "SELECT id FROM principals "
            "WHERE kind = 'system' "
            "AND external_ref = '00000000-0000-0000-0000-000000000000'",
        )
        assert system_principal is not None, "system principal should be seeded"
        system_principal_id = system_principal["id"]

        # The (kind, external_ref) uniqueness that keeps mint + backfill
        # idempotent is enforced: a duplicate system sentinel is rejected.
        with pytest.raises(Exception, match="uq_principals_kind_external_ref"):
            await conn.execute(
                "INSERT INTO principals (kind, external_ref, display_name) "
                "VALUES ('system', "
                "'00000000-0000-0000-0000-000000000000', 'dup')",
            )

        # 4h: a run principal was minted for the pre-existing run.
        run_principal = await conn.fetchrow(
            "SELECT id, display_name FROM principals "
            "WHERE kind = 'run' AND external_ref = $1",
            run_id,
        )
        assert run_principal is not None, (
            "each pre-existing run should get a run principal"
        )
        assert run_principal["display_name"] is None, (
            "run principals carry no display name"
        )

        # 4i: runs.created_by exists, is NOT NULL, and the historical run was
        # backfilled to the system principal.
        migrated_run = await conn.fetchrow(
            "SELECT created_by FROM runs WHERE id = $1", run_id,
        )
        assert migrated_run is not None
        assert migrated_run["created_by"] == system_principal_id, (
            "historical runs must be backfilled to the system principal"
        )

        created_by_col = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'runs' AND column_name = 'created_by'",
        )
        assert created_by_col is not None
        assert created_by_col["is_nullable"] == "NO", (
            "runs.created_by must be NOT NULL after migration"
        )

        # NOT NULL is enforced: a run without created_by is rejected.
        with pytest.raises(Exception, match="created_by"):
            await conn.execute(
                """INSERT INTO runs (intent, autonomy_level)
                   VALUES ('no creator', 'medium')""",
            )

        # FK RESTRICT is enforced: deleting a referenced principal is blocked.
        with pytest.raises(Exception, match="fk_runs_created_by"):
            await conn.execute(
                "DELETE FROM principals WHERE id = $1",
                system_principal_id,
            )

        # 4j: a source principal was minted for the pre-existing source, and
        # sources.created_by was backfilled to the system principal.
        source_principal = await conn.fetchrow(
            "SELECT id, display_name FROM principals "
            "WHERE kind = 'source' AND external_ref = $1",
            source_id,
        )
        assert source_principal is not None, (
            "each pre-existing source should get a source principal"
        )
        assert source_principal["display_name"] == "test-source", (
            "source principals carry the source slug as display_name"
        )

        migrated_source = await conn.fetchrow(
            "SELECT created_by FROM sources WHERE id = $1", source_id,
        )
        assert migrated_source is not None
        assert migrated_source["created_by"] == system_principal_id, (
            "historical sources must be backfilled to the system principal"
        )

        source_created_by_col = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'sources' AND column_name = 'created_by'",
        )
        assert source_created_by_col is not None
        assert source_created_by_col["is_nullable"] == "NO", (
            "sources.created_by must be NOT NULL after migration"
        )

        # NOT NULL is enforced: a source without created_by is rejected.
        with pytest.raises(Exception, match="created_by"):
            await conn.execute(
                """INSERT INTO sources (slug, name)
                   VALUES ('no-creator', 'No Creator')""",
            )

        # FK RESTRICT is enforced on sources.created_by. Deleting the system
        # principal would trip runs' FK first (it too references system), so
        # isolate sources' FK with a dedicated creator principal + source.
        src_creator = await conn.fetchrow(
            "INSERT INTO principals (kind, external_ref, display_name) "
            "VALUES ('source', gen_random_uuid(), 'fk-src-creator') "
            "RETURNING id",
        )
        assert src_creator is not None
        await conn.execute(
            "INSERT INTO sources (slug, name, created_by) VALUES ($1, $2, $3)",
            "fk-src", "FK Src", src_creator["id"],
        )
        with pytest.raises(Exception, match="fk_sources_created_by"):
            await conn.execute(
                "DELETE FROM principals WHERE id = $1",
                src_creator["id"],
            )

        # The baseline sources.slug uniqueness survives the migration: a second
        # source reusing the seeded slug is rejected.
        with pytest.raises(Exception, match="slug"):
            await conn.execute(
                "INSERT INTO sources (slug, name, created_by) "
                "VALUES ($1, $2, $3)",
                "test-source", "Dup Slug", system_principal_id,
            )

        # 4k: a consumer principal was minted for the pre-existing consumer,
        # backfilled to ITS OWN principal (not the system principal).
        consumer_principal = await conn.fetchrow(
            "SELECT id, display_name FROM principals "
            "WHERE kind = 'consumer' AND external_ref = $1",
            consumer_id,
        )
        assert consumer_principal is not None, (
            "each pre-existing consumer should get its own consumer principal"
        )
        assert consumer_principal["display_name"] == "test-consumer", (
            "consumer principals carry the consumer name as display_name"
        )
        assert consumer_principal["id"] != system_principal_id, (
            "a consumer must be backfilled to its OWN principal, not system"
        )

        migrated_consumer = await conn.fetchrow(
            "SELECT principal_id FROM consumers WHERE id = $1", consumer_id,
        )
        assert migrated_consumer is not None
        assert migrated_consumer["principal_id"] == consumer_principal["id"], (
            "each consumer must point at its own minted principal"
        )

        consumer_principal_col = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'consumers' AND column_name = 'principal_id'",
        )
        assert consumer_principal_col is not None
        assert consumer_principal_col["is_nullable"] == "NO", (
            "consumers.principal_id must be NOT NULL after migration"
        )

        # NOT NULL is enforced: a consumer without principal_id is rejected.
        with pytest.raises(Exception, match="principal_id"):
            await conn.execute(
                """INSERT INTO consumers (name, trust_tier)
                   VALUES ('no principal', 'identified')""",
            )

        # UNIQUE (principal_id) is enforced: a second consumer cannot reuse a
        # principal already claimed by another consumer.
        with pytest.raises(Exception, match="uq_consumers_principal_id"):
            await conn.execute(
                """INSERT INTO consumers (name, trust_tier, principal_id)
                   VALUES ('dup principal', 'identified', $1)""",
                consumer_principal["id"],
            )

        # FK RESTRICT is enforced on consumers.principal_id.
        with pytest.raises(Exception, match="fk_consumers_principal"):
            await conn.execute(
                "DELETE FROM principals WHERE id = $1",
                consumer_principal["id"],
            )

        # 4l: events.principal_id backfill maps every legacy shape per rule.
        run_principal_id = run_principal["id"]
        source_principal_id = source_principal["id"]

        async def _event_principal(eid: Any) -> Any:
            return await conn.fetchval(
                "SELECT principal_id FROM events WHERE id = $1", eid,
            )

        # (1) run-attached 'internal' event -> the run's principal.
        assert await _event_principal(event_id) == run_principal_id, (
            "a run-attached event must attribute to the run principal"
        )
        # (2) overseer-sourced event that carries a run_id -> run principal.
        assert await _event_principal(event_overseer_id) == run_principal_id, (
            "a run-attached overseer event must attribute to the run principal"
        )
        # (3) dispatch-worker event, no run -> system principal.
        assert await _event_principal(event_worker_id) == system_principal_id, (
            "a run-less dispatch-worker event must attribute to system"
        )
        # (4) webhook event whose source slug exists -> the source principal.
        assert await _event_principal(event_webhook_id) == source_principal_id, (
            "a run-less webhook event must attribute to its source principal"
        )
        # (5) orphaned-slug event (source row gone) -> system principal.
        assert await _event_principal(event_orphan_id) == system_principal_id, (
            "an orphaned-slug event must fall back to the system principal"
        )

        # The backfill left NO event unattributed: every historical event got a
        # principal before the SET NOT NULL could bite. (The NOT NULL below is
        # the hard gate; this count is the explicit completeness witness.)
        null_principal_events = await conn.fetchval(
            "SELECT count(*) FROM events WHERE principal_id IS NULL",
        )
        assert null_principal_events == 0, (
            "no event may remain with a NULL principal_id after the backfill"
        )

        # The append-only trigger was re-enabled after the backfill.
        deny_state = await conn.fetchval(
            "SELECT t.tgenabled::text FROM pg_trigger t "
            "JOIN pg_class c ON c.oid = t.tgrelid "
            "WHERE c.relname = 'events' AND t.tgname = 'deny_mutation'",
        )
        assert deny_state == "O", (
            "the events deny_mutation trigger must be re-enabled after backfill"
        )

        # events.principal_id is NOT NULL after migration.
        principal_col = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'events' AND column_name = 'principal_id'",
        )
        assert principal_col is not None
        assert principal_col["is_nullable"] == "NO", (
            "events.principal_id must be NOT NULL after migration"
        )

        # NOT NULL is enforced: an event without principal_id is rejected.
        with pytest.raises(Exception, match="principal_id"):
            await conn.execute(
                """INSERT INTO events (run_id, event_type)
                   VALUES ($1, 'no.actor')""",
                run_id,
            )

        # The old source column is gone.
        events_cols_post = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'events'",
        )
        assert "source" not in {c["column_name"] for c in events_cols_post}, (
            "events.source must be dropped after migration"
        )

        # The new principal index exists; the old source index is gone.
        events_indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'events'",
        )
        index_names = {r["indexname"] for r in events_indexes}
        assert "idx_events_principal_created" in index_names, (
            "the principal_id index must exist after migration"
        )
        assert "idx_events_source_created" not in index_names, (
            "the old source index must be dropped"
        )

        # The NOTIFY function body no longer references source.
        notify_body = await conn.fetchval(
            "SELECT pg_get_functiondef(oid) FROM pg_proc "
            "WHERE proname = 'notify_orxtra_event'",
        )
        assert "principal_id" in notify_body, (
            "the NOTIFY function must emit principal_id"
        )
        assert "NEW.source" not in notify_body, (
            "the NOTIFY function must no longer reference source"
        )

        # 4m: inbox_items.resolved_by exists, is NULLABLE, and historical rows
        # (both the pre-answered and the pending item) stay NULL -- no backfill.
        resolved_by_col = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'inbox_items' AND column_name = 'resolved_by'",
        )
        assert resolved_by_col is not None
        assert resolved_by_col["is_nullable"] == "YES", (
            "inbox_items.resolved_by must be NULLABLE after migration"
        )

        answered_resolved_by = await conn.fetchval(
            "SELECT resolved_by FROM inbox_items WHERE id = $1",
            inbox_answered_id,
        )
        assert answered_resolved_by is None, (
            "a historical answered item must keep resolved_by NULL "
            "(resolutions predating attribution are not backfilled)"
        )

        pending_resolved_by = await conn.fetchval(
            "SELECT resolved_by FROM inbox_items WHERE id = $1",
            inbox_pending_id,
        )
        assert pending_resolved_by is None, (
            "a pending item has no resolver -- resolved_by stays NULL"
        )

        # A POST-migration resolution stamps resolved_by with a valid principal.
        await conn.execute(
            "UPDATE inbox_items SET status = 'answered', answer = 'ok', "
            "answered_at = now(), resolved_by = $1 WHERE id = $2",
            system_principal_id, inbox_pending_id,
        )
        post_resolved_by = await conn.fetchval(
            "SELECT resolved_by FROM inbox_items WHERE id = $1",
            inbox_pending_id,
        )
        assert post_resolved_by == system_principal_id, (
            "a post-migration resolution must record the resolving principal"
        )

        # FK RESTRICT is enforced on inbox_items.resolved_by. Isolate it with a
        # dedicated resolver principal so runs' FK (also -> system) is untouched.
        inbox_resolver = await conn.fetchrow(
            "INSERT INTO principals (kind, external_ref, display_name) "
            "VALUES ('consumer', gen_random_uuid(), 'fk-inbox-resolver') "
            "RETURNING id",
        )
        assert inbox_resolver is not None
        await conn.execute(
            "UPDATE inbox_items SET resolved_by = $1 WHERE id = $2",
            inbox_resolver["id"], inbox_answered_id,
        )
        with pytest.raises(Exception, match="fk_inbox_resolved_by"):
            await conn.execute(
                "DELETE FROM principals WHERE id = $1",
                inbox_resolver["id"],
            )

        # The resolved_by index exists.
        inbox_indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'inbox_items'",
        )
        assert "idx_inbox_resolved_by" in {
            r["indexname"] for r in inbox_indexes
        }, "the resolved_by index must exist after migration"

        # 4n: subscription ownership cutover. The seeded subscription carried
        # owner_run_id; after migration it is attributed to the system principal
        # (ownership history is unknowable), owner_run_id is gone, and the FK
        # CASCADEs so deleting an owning principal deletes its subscriptions.
        migrated_sub = await conn.fetchrow(
            "SELECT principal_id FROM subscriptions WHERE id = $1", sub_id,
        )
        assert migrated_sub is not None
        assert migrated_sub["principal_id"] == system_principal_id, (
            "historical subscriptions must be backfilled to the system principal "
            "(owner_run_id ownership history is discarded)"
        )

        sub_principal_col = await conn.fetchrow(
            "SELECT is_nullable FROM information_schema.columns "
            "WHERE table_name = 'subscriptions' AND column_name = 'principal_id'",
        )
        assert sub_principal_col is not None
        assert sub_principal_col["is_nullable"] == "NO", (
            "subscriptions.principal_id must be NOT NULL after migration"
        )

        # owner_run_id and its index are gone.
        sub_cols_post = await conn.fetch(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'subscriptions'",
        )
        assert "owner_run_id" not in {c["column_name"] for c in sub_cols_post}, (
            "subscriptions.owner_run_id must be dropped after migration"
        )
        sub_indexes = await conn.fetch(
            "SELECT indexname FROM pg_indexes WHERE tablename = 'subscriptions'",
        )
        sub_index_names = {r["indexname"] for r in sub_indexes}
        assert "idx_subscriptions_principal_id" in sub_index_names, (
            "the principal_id index must exist after migration"
        )
        assert "idx_subscriptions_owner_run_id" not in sub_index_names, (
            "the old owner_run_id index must be dropped"
        )

        # NOT NULL is enforced: a subscription without principal_id is rejected.
        with pytest.raises(Exception, match="principal_id"):
            await conn.execute(
                "INSERT INTO subscriptions (storage) VALUES ('persistent')",
            )

        # ON DELETE CASCADE: deleting an owning principal deletes its
        # subscriptions. Use a dedicated owner principal + subscription so the
        # cascade is isolated from the system principal (pinned by RESTRICT FKs).
        sub_owner = await conn.fetchrow(
            "INSERT INTO principals (kind, external_ref, display_name) "
            "VALUES ('consumer', gen_random_uuid(), 'cascade-owner') "
            "RETURNING id",
        )
        assert sub_owner is not None
        cascade_sub_id = await conn.fetchval(
            "INSERT INTO subscriptions (storage, principal_id) "
            "VALUES ('persistent', $1) RETURNING id",
            sub_owner["id"],
        )
        await conn.execute(
            "DELETE FROM principals WHERE id = $1", sub_owner["id"],
        )
        cascade_survivor = await conn.fetchrow(
            "SELECT id FROM subscriptions WHERE id = $1", cascade_sub_id,
        )
        assert cascade_survivor is None, (
            "deleting an owning principal must CASCADE-delete its subscriptions"
        )

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
