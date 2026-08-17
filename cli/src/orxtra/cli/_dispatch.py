"""CLI entry points for dispatch commands.

Provides ``register_dispatch_commands`` to wire dispatch subcommands
into the main orxtra CLI app. Currently: ``orxtra dispatch run``.

Lives in cli/ (interfaces layer) because it bridges dispatch
(orchestration) with services (composition) for construction.
"""

from __future__ import annotations

import asyncio
import signal
import sys

import strictcli

# Handlers absorb the app-level global flag values they do not name.
_ABSORBS_GLOBALS = strictcli.Forwarding(
    reason="absorbs app-level global flag values the handler does not name",
)

# Handler-side fallbacks for the optional operational flags of `dispatch run`.
# They cannot be flag defaults: the command is mutating, and strictcli forbids
# a value default there.
_DEFAULT_POLL_INTERVAL = 5.0
_DEFAULT_BATCH_SIZE = 100


def register_dispatch_commands(app: strictcli.App) -> None:
    """Register the ``dispatch`` command group on the orxtra CLI."""
    dispatch_group = app.group(
        "dispatch",
        help="Manage the persistent event dispatch worker process.",
    )

    @dispatch_group.command(
        name="run",
        help="Start the long-running dispatch worker that delivers stored events to "
        "their subscriptions and executes the action chains those subscriptions "
        "declare. Wakes on PostgreSQL LISTEN/NOTIFY and falls back to polling every "
        "--poll-interval seconds, up to --batch-size events a batch. Use --cursor to "
        "run several workers over independent positions.",
        effect="mutating",
        forwarding=_ABSORBS_GLOBALS,
    )
    @strictcli.flag(
        name="cursor",
        type=str,
        help="Named cursor for this worker instance (enables multiple workers). "
        "Omitted, the worker runs on the cursor named 'main'.",
        presence="optional",
    )
    @strictcli.flag(
        name="poll-interval",
        type=float,
        help="Fallback polling interval in seconds when LISTEN/NOTIFY is idle. "
        "Omitted, the worker polls every 5 seconds.",
        presence="optional",
    )
    @strictcli.flag(
        name="batch-size",
        type=int,
        help="Maximum number of events to process in a single polling batch. "
        "Omitted, the worker processes up to 100 events a batch.",
        presence="optional",
    )
    def cmd_dispatch_run(
        _ctx: strictcli.Context, *,
        db: str | None,
        cursor: str | None,
        poll_interval: float | None,
        batch_size: int | None,
        **_kwargs: object,
    ) -> None:
        if db is None:
            print("--db is required for dispatch run", file=sys.stderr)
            sys.exit(1)

        # The three operational knobs are declared optional rather than
        # defaulted: `dispatch run` is a mutating command, and strictcli
        # refuses a value default there. The fallbacks live here and are
        # stated in each flag's help.
        cursor_name = "main" if cursor is None else cursor
        interval = _DEFAULT_POLL_INTERVAL if poll_interval is None else poll_interval
        batch = _DEFAULT_BATCH_SIZE if batch_size is None else batch_size

        async def _run() -> None:
            import asyncpg as _asyncpg
            from orxtra.services import (
                SchemaError,
                create_dispatch_worker,
                verify_schema,
            )

            pool = await _asyncpg.create_pool(db)
            try:
                try:
                    await verify_schema(pool)
                except SchemaError as exc:
                    print(str(exc), file=sys.stderr)
                    sys.exit(1)

                from orxtra.notification import PgNotificationBackend

                notification_port = PgNotificationBackend(pool)
                worker = await create_dispatch_worker(
                    pool,
                    notification_port=notification_port,
                    cursor_name=cursor_name,
                    poll_interval=interval,
                    batch_size=batch,
                )

                # Graceful stop on SIGINT/SIGTERM.
                loop = asyncio.get_running_loop()
                for sig in (signal.SIGINT, signal.SIGTERM):
                    loop.add_signal_handler(
                        sig,
                        lambda: asyncio.create_task(worker.stop()),
                    )

                await worker.run()
            finally:
                await pool.close()

        asyncio.run(_run())
