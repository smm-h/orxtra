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


def register_dispatch_commands(app: strictcli.App) -> None:
    """Register the ``dispatch`` command group on the orxtra CLI."""
    dispatch_group = app.group(
        "dispatch",
        help="Manage the persistent event dispatch worker process.",
    )

    @dispatch_group.command(
        name="run",
        help="Start the long-running dispatch worker that delivers stored events to their "
        "subscriptions and executes the action chains those subscriptions declare. Wakes "
        "on PostgreSQL LISTEN/NOTIFY and falls back to polling every --poll-interval "
        "seconds, up to --batch-size events a batch. Use --cursor to run several "
        "workers over independent positions.",
        effect="mutating",
        forwarding=_ABSORBS_GLOBALS,
    )
    @strictcli.flag(
        name="cursor",
        type=str,
        help="Named cursor for this worker instance (enables multiple workers).",
        default="main",
    )
    @strictcli.flag(
        name="poll-interval",
        type=float,
        help="Fallback polling interval in seconds when LISTEN/NOTIFY is idle.",
        default=5.0,
    )
    @strictcli.flag(
        name="batch-size",
        type=int,
        help="Maximum number of events to process in a single polling batch.",
        default=100,
    )
    def cmd_dispatch_run(
        _ctx: strictcli.Context, *,
        db: str,
        cursor: str,
        **kwargs: object,
    ) -> None:
        if not db:
            print("--db is required for dispatch run", file=sys.stderr)
            sys.exit(1)

        poll_interval: float = kwargs.get("poll_interval", 5.0)  # type: ignore[assignment]
        batch_size: int = kwargs.get("batch_size", 100)  # type: ignore[assignment]

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
                    cursor_name=cursor,
                    poll_interval=poll_interval,
                    batch_size=batch_size,
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
