"""CLI db command group for database provisioning and migration.

Registers ``db init``, ``db verify``, and ``db migrate`` (plan/apply/status)
commands on the orxtra CLI. Uses the pgdesign-generated schema executor for
init/verify, and wraps ``pgdesign migrate`` subcommands for migrations.

Layering note: the generated executor ships inside the orxtra namespace at
``orxtra.services._generated`` so it resolves in both the editable dev tree
and an installed wheel. The shared asyncpg adapter lives in
``orxtra.services._schema``.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import asyncpg
import strictcli
from orxtra.services import AsyncpgAdapter

# Handlers absorb the app-level global flag values they do not name.
_ABSORBS_GLOBALS = strictcli.Forwarding(
    reason="absorbs app-level global flag values the handler does not name",
)


def _die(message: str) -> NoReturn:
    print(message, file=sys.stderr)
    sys.exit(1)


def _require_db(db: str) -> str:
    if not db:
        _die("--db is required for this command")
    return db


def _schema_dir() -> str:
    """Return the path to the schema/ directory."""
    return str(Path(__file__).resolve().parents[5] / "schema")


def _find_pgdesign() -> str:
    """Locate the pgdesign binary on PATH."""
    import shutil

    path = shutil.which("pgdesign")
    if path is None:
        _die("pgdesign binary not found on PATH")
    return path


def _run_pgdesign(
    subcommand: str,
    db_url: str,
    extra_args: list[str] | None = None,
) -> None:
    """Run a pgdesign migrate subcommand and exit with its code."""
    schema_path = _schema_dir()
    cmd = [
        _find_pgdesign(),
        "migrate", subcommand,
    ]
    # plan takes positional path arg; apply/status do not.
    if subcommand == "plan":
        cmd.append(schema_path)
    cmd.extend([
        "--db", db_url,
        "--dir", str(Path(schema_path) / "migrations"),
    ])
    if extra_args:
        cmd.extend(extra_args)
    result = subprocess.run(cmd, check=False)  # noqa: S603
    sys.exit(result.returncode)


def _register_migrate_commands(
    db_group: strictcli.Group,
) -> None:
    """Register the ``db migrate`` subgroup."""
    migrate_group = db_group.group(
        "migrate",
        help="Database migration commands (wraps pgdesign).",
    )

    @migrate_group.command(
        name="plan",
        help="Preview schema changes without generating files.",
        effect="read_only",
        forwarding=_ABSORBS_GLOBALS,
    )
    def cmd_db_migrate_plan(
        _ctx: strictcli.Context, *, db: str, **_kwargs: object,
    ) -> None:
        _run_pgdesign("plan", _require_db(db))

    @migrate_group.command(
        name="apply",
        help="Apply pending migrations to the database.",
        effect="mutating",
        consequential=True,
        forwarding=_ABSORBS_GLOBALS,
    )
    def cmd_db_migrate_apply(
        ctx: strictcli.Context, *, db: str, **_kwargs: object,
    ) -> None:
        # --dry-run is the framework-owned reserved flag; its value arrives on
        # the context and is forwarded to pgdesign, which has its own.
        extra = ["--dry-run"] if ctx.dry_run else ["--no-dry-run"]
        _run_pgdesign("apply", _require_db(db), extra)

    @migrate_group.command(
        name="status",
        help="Show applied and pending migration status.",
        effect="read_only",
        forwarding=_ABSORBS_GLOBALS,
    )
    def cmd_db_migrate_status(
        _ctx: strictcli.Context, *, db: str, **_kwargs: object,
    ) -> None:
        _run_pgdesign("status", _require_db(db))


def register_db_commands(app: strictcli.App) -> None:
    """Register the ``db`` command group on the orxtra CLI."""
    db_group = app.group(
        "db",
        help="Database provisioning, schema verification, and migration commands.",
    )

    @db_group.command(
        name="init",
        help="Create the database schema and seed the system principal (idempotent).",
        effect="mutating",
        forwarding=_ABSORBS_GLOBALS,
    )
    def cmd_db_init(
        ctx: strictcli.Context, *, db: str, **_kwargs: object,
    ) -> None:
        db_url = _require_db(db)

        async def _run() -> None:
            from orxtra.services._generated.schema_executor import (
                ensure_schema,
            )

            conn = await asyncpg.connect(db_url)
            try:
                adapter = AsyncpgAdapter(conn)
                # AsyncpgAdapter satisfies AsyncConnection at runtime; the
                # generated protocol mistypes transaction() as async def.
                # Known pgdesign generated-protocol gap (filed upstream).
                result = await ensure_schema(adapter)  # type: ignore[arg-type]
                if result.errors:
                    for kind, name, err in result.errors:
                        print(
                            f"ERROR {kind}.{name}: {err}",
                            file=sys.stderr,
                        )
                    sys.exit(1)
                if not ctx.quiet:
                    n_exec = len(result.executed)
                    n_skip = len(result.skipped)
                    print(
                        f"Schema initialized: "
                        f"{n_exec} executed, {n_skip} skipped.",
                    )
            finally:
                await conn.close()

            # Seed the singleton system principal (idempotent via mint).
            # PgPrincipalStorage needs a pool, so open a short-lived one.
            from orxtra.identity import PgPrincipalStorage
            from orxtra.protocols import (
                KIND_SYSTEM,
                SYSTEM_PRINCIPAL_EXTERNAL_REF,
            )

            pool = await asyncpg.create_pool(db_url)
            try:
                storage = PgPrincipalStorage(pool)
                await storage.mint_principal(
                    KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
                )
            finally:
                await pool.close()
            if not ctx.quiet:
                print("System principal seeded.")

        asyncio.run(_run())

    @db_group.command(
        name="verify",
        help="Verify that all expected database schema objects are present.",
        effect="read_only",
        forwarding=_ABSORBS_GLOBALS,
    )
    def cmd_db_verify(
        ctx: strictcli.Context, *, db: str, **_kwargs: object,
    ) -> None:
        db_url = _require_db(db)

        async def _run() -> None:
            from orxtra.services import verify_schema_objects

            conn = await asyncpg.connect(db_url)
            try:
                adapter = AsyncpgAdapter(conn)
                present, missing = await verify_schema_objects(adapter)
                if not ctx.quiet:
                    print(
                        f"Schema verification: "
                        f"{len(present)} present, "
                        f"{len(missing)} missing.",
                    )
                if missing:
                    if not ctx.quiet:
                        for kind, name in missing:
                            print(f"  MISSING {kind}: {name}")
                        print(
                            "\nRun 'orxtra db init' to create "
                            "missing objects, or "
                            "'orxtra db migrate apply' to apply "
                            "pending migrations.",
                        )
                    sys.exit(1)
            finally:
                await conn.close()

        asyncio.run(_run())

    _register_migrate_commands(db_group)
