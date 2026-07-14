"""Factory for constructing a DispatchWorker with concrete implementations.

Bridges the dispatch layer's protocol-based DispatchWorker with the
services layer's concrete implementations: PgDispatchBackend,
ServicesActionExecutor, AsyncioFlushScheduler.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.dispatch import DispatchWorker, PgDispatchBackend
from orxtra.identity import PgPrincipalStorage
from orxtra.protocols import KIND_SOURCE
from orxtra.services._actions import ServicesActionExecutor
from orxtra.services._flush import AsyncioFlushScheduler
from orxtra.trace import EVENTS_CHANNEL

if TYPE_CHECKING:
    from collections.abc import Sequence
    from uuid import UUID

    import asyncpg
    from orxtra.dispatch import SourcePrincipalResolver
    from orxtra.protocols import NotificationPort


def _make_source_principal_resolver(
    backend: PgDispatchBackend,
    principal_storage: PgPrincipalStorage,
) -> SourcePrincipalResolver:
    """Build a resolver: source slugs -> source-principal ids.

    Each slug is looked up as a source; the source's principal (minted at
    source birth as KIND_SOURCE/external_ref=source.id) supplies the id.
    Slugs with no source, or sources without a principal, contribute nothing.
    """

    async def _resolve(slugs: Sequence[str]) -> set[UUID]:
        result: set[UUID] = set()
        for slug in slugs:
            source = await backend.get_source_by_slug(slug)
            if source is None:
                continue
            principal = await principal_storage.get_principal_by_ref(
                KIND_SOURCE, source.id,
            )
            if principal is not None:
                result.add(principal.id)
        return result

    return _resolve


async def create_dispatch_worker(
    pool: asyncpg.Pool,
    *,
    notification_port: NotificationPort | None = None,
    cursor_name: str = "main",
    poll_interval: float = 5.0,
    batch_size: int = 100,
) -> DispatchWorker:
    """Construct a DispatchWorker with all concrete service implementations.

    Args:
        pool: asyncpg connection pool.
        notification_port: optional notification delivery port for NotifyAction.
        cursor_name: name for the durable cursor (supports multiple workers).
        poll_interval: fallback poll interval in seconds.
        batch_size: max events per poll.

    Returns:
        A fully-wired DispatchWorker ready to ``run()``.
    """
    backend = PgDispatchBackend(pool)
    action_executor = ServicesActionExecutor(pool, intent_prefix="dispatch")
    flush_scheduler = AsyncioFlushScheduler()

    principal_storage = PgPrincipalStorage(pool)
    resolver = _make_source_principal_resolver(backend, principal_storage)

    return DispatchWorker(
        backend=backend,
        action_executor=action_executor,
        flush_scheduler=flush_scheduler,
        pool=pool,
        cursor_name=cursor_name,
        events_channel=EVENTS_CHANNEL,
        source_principal_resolver=resolver,
        notification_port=notification_port,
        poll_interval=poll_interval,
        batch_size=batch_size,
    )
