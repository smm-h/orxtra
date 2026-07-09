"""CLI db command group for database provisioning and migration.

Registers ``db init``, ``db verify``, and ``db migrate`` (plan/apply/status)
commands on the orxtra CLI. Uses the pgdesign-generated schema executor for
init/verify, and wraps ``pgdesign migrate`` subcommands for migrations.

Layering note: the generated executor lives in ``schema/_generated/`` which
is a dev_node (not a proper installed package). The shared asyncpg adapter
and pg_uuidv7 stub live in ``orxtra.services._schema``; importing that
module adds schema/ to sys.path.
"""

from __future__ import annotations

import asyncio
import subprocess
import sys
from pathlib import Path
from typing import NoReturn

import asyncpg
import strictcli
from orxtra.services import PG_UUIDV7_STUB, AsyncpgAdapter


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
    )
    def cmd_db_migrate_plan(
        *, db: str, **_kwargs: object,
    ) -> None:
        _run_pgdesign("plan", _require_db(db))

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
        dry_run: bool = kwargs.get("dry_run", False)  # type: ignore[assignment]
        extra = ["--dry-run"] if dry_run else ["--no-dry-run"]
        _run_pgdesign("apply", _require_db(db), extra)

    @migrate_group.command(
        name="status",
        help="Show applied and pending migration status.",
    )
    def cmd_db_migrate_status(
        *, db: str, **_kwargs: object,
    ) -> None:
        _run_pgdesign("status", _require_db(db))


def register_db_commands(app: strictcli.App) -> None:
    """Register the ``db`` command group on the orxtra CLI."""
    db_group = app.group(
        "db",
        help="Database provisioning and migration commands.",
    )

    @db_group.command(
        name="init",
        help="Create the database schema (idempotent).",
    )
    @strictcli.flag(
        name="use-extension-stub",
        type=bool,
        default=False,
        help=(
            "Use a gen_random_uuid() stub instead of "
            "pg_uuidv7 extension."
        ),
    )
    def cmd_db_init(
        *, db: str, quiet: bool, **kwargs: object,
    ) -> None:
        db_url = _require_db(db)
        use_stub: bool = kwargs.get(  # type: ignore[assignment]
            "use_extension_stub", False,
        )

        async def _run() -> None:
            from _generated.schema_executor import (  # type: ignore[import-not-found]
                ensure_schema,
                execute,
            )

            conn = await asyncpg.connect(db_url)
            try:
                adapter = AsyncpgAdapter(conn)
                if use_stub:
                    result = await execute(
                        adapter,
                        idempotent=True,
                        extension_stubs={
                            "pg_uuidv7": PG_UUIDV7_STUB,
                        },
                    )
                else:
                    result = await ensure_schema(adapter)
                if result.errors:
                    for kind, name, err in result.errors:
                        print(
                            f"ERROR {kind}.{name}: {err}",
                            file=sys.stderr,
                        )
                    sys.exit(1)
                if not quiet:
                    n_exec = len(result.executed)
                    n_skip = len(result.skipped)
                    print(
                        f"Schema initialized: "
                        f"{n_exec} executed, {n_skip} skipped.",
                    )
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
            from _generated.schema_executor import (
                verify,
            )

            conn = await asyncpg.connect(db_url)
            try:
                adapter = AsyncpgAdapter(conn)
                result = await verify(adapter)
                n_miss = len(result.missing)
                n_present = len(result.present)
                if not quiet:
                    print(
                        f"Schema verification: "
                        f"{n_present} present, "
                        f"{n_miss} missing.",
                    )
                if result.missing:
                    if not quiet:
                        for kind, name in result.missing:
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
