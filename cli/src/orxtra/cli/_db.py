"""CLI db command group for database provisioning and migration.

Registers ``db init``, ``db verify``, and ``db migrate`` (plan/apply/status)
commands on the orxtra CLI. Uses the pgdesign-generated schema executor for
init/verify, and wraps ``pgdesign migrate`` subcommands for migrations.

Layering note: the generated executor lives in ``schema/_generated/`` which
is a dev_node (not a proper installed package). We add ``schema/`` to
sys.path at import time, mirroring the approach in ``conftest.py``. The
asyncpg adapter bridges asyncpg.Connection to the executor's AsyncConnection
protocol.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, Self

import asyncpg
import strictcli

if TYPE_CHECKING:
    import types

# Add schema/ to sys.path so _generated.schema_executor is importable.
_SCHEMA_DIR = str(Path(__file__).resolve().parents[5] / "schema")
if _SCHEMA_DIR not in sys.path:
    sys.path.append(_SCHEMA_DIR)


# ---- asyncpg adapter (mirrors tests/pg_fixtures.py) ----


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


# pg_uuidv7 extension stub: standard PG images lack pg_uuidv7.
# The generated DDL expects CREATE EXTENSION pg_uuidv7; this stub replaces
# it with a function mapping uuid_generate_v7() to gen_random_uuid().
_PG_UUIDV7_STUB = """\
CREATE OR REPLACE FUNCTION uuid_generate_v7() RETURNS uuid AS $$
    SELECT gen_random_uuid();
$$ LANGUAGE sql;
"""


def _die(message: str) -> None:
    print(message, file=sys.stderr)
    sys.exit(1)


def _require_db(db: str) -> str:
    if not db:
        _die("--db is required for this command")
    return db


def _schema_dir() -> str:
    """Return the path to the schema/ directory containing TOML files."""
    return str(Path(__file__).resolve().parents[5] / "schema")


def register_db_commands(app: strictcli.App) -> None:
    """Register the ``db`` command group on the orxtra CLI."""
    db_group = app.group("db", help="Database provisioning and migration commands.")

    @db_group.command(
        name="init",
        help="Create the database schema (idempotent).",
    )
    @strictcli.flag(
        name="use-extension-stub",
        type=bool,
        default=False,
        help="Use a gen_random_uuid() stub instead of pg_uuidv7 extension.",
    )
    def cmd_db_init(
        *, db: str, quiet: bool, **kwargs: object,
    ) -> None:
        db_url = _require_db(db)
        use_stub: bool = kwargs.get("use_extension_stub", False)  # type: ignore[assignment]

        async def _run() -> None:
            from _generated.schema_executor import (  # noqa: PLC0415
                ensure_schema,
                execute,
            )

            conn = await asyncpg.connect(db_url)
            try:
                adapter = _AsyncpgAdapter(conn)
                if use_stub:
                    result = await execute(
                        adapter,
                        idempotent=True,
                        extension_stubs={"pg_uuidv7": _PG_UUIDV7_STUB},
                    )
                else:
                    result = await ensure_schema(adapter)
                if result.errors:
                    for kind, name, err in result.errors:
                        print(f"ERROR {kind}.{name}: {err}", file=sys.stderr)
                    sys.exit(1)
                if not quiet:
                    created = len(result.executed)
                    skipped = len(result.skipped)
                    print(f"Schema initialized: {created} executed, {skipped} skipped.")
            finally:
                await conn.close()

        asyncio.run(_run())

    @db_group.command(
        name="verify",
        help="Verify the database schema is complete.",
    )
    def cmd_db_verify(
        *, db: str, quiet: bool, **_kwargs: object,
    ) -> None:
        db_url = _require_db(db)

        async def _run() -> None:
            from _generated.schema_executor import (  # noqa: PLC0415
                verify,
            )

            conn = await asyncpg.connect(db_url)
            try:
                adapter = _AsyncpgAdapter(conn)
                result = await verify(adapter)
                missing_count = len(result.missing)
                present_count = len(result.present)
                if not quiet:
                    print(f"Schema verification: {present_count} present, {missing_count} missing.")
                if result.missing:
                    if not quiet:
                        for kind, name in result.missing:
                            print(f"  MISSING {kind}: {name}")
                        print(
                            "\nRun 'orxtra db init' to create missing objects, "
                            "or 'orxtra db migrate apply' to apply pending migrations.",
                        )
                    sys.exit(1)
            finally:
                await conn.close()

        asyncio.run(_run())

    # -- db migrate subgroup --

    migrate_group = db_group.group("migrate", help="Database migration commands (wraps pgdesign migrate).")

    @migrate_group.command(
        name="plan",
        help="Preview schema changes without generating migration files.",
    )
    def cmd_db_migrate_plan(
        *, db: str, **_kwargs: object,
    ) -> None:
        db_url = _require_db(db)
        schema_path = _schema_dir()
        cmd = [
            sys.executable, "-m", "pgdesign",
            "migrate", "plan",
            schema_path,
            "--db", db_url,
            "--dir", str(Path(schema_path) / "migrations"),
        ]
        result = subprocess.run(cmd, check=False)  # noqa: S603
        sys.exit(result.returncode)

    @migrate_group.command(
        name="apply",
        help="Apply pending migrations to the database.",
    )
    @strictcli.flag(
        name="dry-run",
        type=bool,
        default=False,
        help="Preview migration SQL without executing.",
    )
    def cmd_db_migrate_apply(
        *, db: str, **kwargs: object,
    ) -> None:
        db_url = _require_db(db)
        dry_run: bool = kwargs.get("dry_run", False)  # type: ignore[assignment]
        schema_path = _schema_dir()
        cmd = [
            sys.executable, "-m", "pgdesign",
            "migrate", "apply",
            "--db", db_url,
            "--dir", str(Path(schema_path) / "migrations"),
        ]
        if dry_run:
            cmd.append("--dry-run")
        else:
            cmd.append("--no-dry-run")
        result = subprocess.run(cmd, check=False)  # noqa: S603
        sys.exit(result.returncode)

    @migrate_group.command(
        name="status",
        help="Show applied and pending migration status.",
    )
    def cmd_db_migrate_status(
        *, db: str, **_kwargs: object,
    ) -> None:
        db_url = _require_db(db)
        schema_path = _schema_dir()
        cmd = [
            sys.executable, "-m", "pgdesign",
            "migrate", "status",
            "--db", db_url,
            "--dir", str(Path(schema_path) / "migrations"),
        ]
        result = subprocess.run(cmd, check=False)  # noqa: S603
        sys.exit(result.returncode)
