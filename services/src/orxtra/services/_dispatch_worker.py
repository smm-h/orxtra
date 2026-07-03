"""Factory for constructing a DispatchWorker with concrete implementations.

Bridges the dispatch layer's protocol-based DispatchWorker with the
services layer's concrete implementations: PgDispatchBackend,
ServicesActionExecutor, AsyncioFlushScheduler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.dispatch import DispatchWorker, PgDispatchBackend
from orxtra.services._actions import ServicesActionExecutor
from orxtra.services._flush import AsyncioFlushScheduler
from orxtra.trace import EVENTS_CHANNEL

if TYPE_CHECKING:
    import asyncpg


def create_dispatch_worker(
    pool: asyncpg.Pool,
    *,
    cursor_name: str = "main",
    poll_interval: float = 5.0,
    batch_size: int = 100,
) -> DispatchWorker:
    """Construct a DispatchWorker with all concrete service implementations.

    Args:
        pool: asyncpg connection pool.
        cursor_name: name for the durable cursor (supports multiple workers).
        poll_interval: fallback poll interval in seconds.
        batch_size: max events per poll.

    Returns:
        A fully-wired DispatchWorker ready to ``run()``.
    """
    backend = PgDispatchBackend(pool)
    action_executor = ServicesActionExecutor(pool, intent_prefix="dispatch")
    flush_scheduler = AsyncioFlushScheduler()

    return DispatchWorker(
        backend=backend,
        action_executor=action_executor,
        flush_scheduler=flush_scheduler,
        pool=pool,
        cursor_name=cursor_name,
        events_channel=EVENTS_CHANNEL,
        poll_interval=poll_interval,
        batch_size=batch_size,
    )
