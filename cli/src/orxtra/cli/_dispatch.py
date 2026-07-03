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


def register_dispatch_commands(app: strictcli.App) -> None:
    """Register the ``dispatch`` command group on the orxtra CLI."""
    dispatch_group = app.group("dispatch", help="Dispatch worker commands.")

    @dispatch_group.command(
        name="run",
        help="Run the persistent dispatch worker.",
    )
    @strictcli.flag(
        name="cursor",
        type=str,
        help="Cursor name for this worker instance.",
        default="main",
    )
    @strictcli.flag(
        name="poll-interval",
        type=float,
        help="Fallback poll interval in seconds.",
        default=5.0,
    )
    @strictcli.flag(
        name="batch-size",
        type=int,
        help="Max events per polling batch.",
        default=100,
    )
    def cmd_dispatch_run(
        *,
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
            import asyncpg as _asyncpg  # noqa: PLC0415
            from orxtra.services import (  # noqa: PLC0415
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

                worker = create_dispatch_worker(
                    pool,
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
