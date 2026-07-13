from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from orxtra.dispatch._types import FilterPredicate

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Sequence
    from uuid import UUID

logger = logging.getLogger(__name__)

# Resolves a list of source slugs (a subscription filter's authoring interface)
# to the set of source-principal UUIDs those slugs designate. Injected from the
# services layer so dispatch stays protocol-only (no identity import). Events
# carry principal_id; subscriptions filter by slug; this callback bridges them.
type SourcePrincipalResolver = Callable[
    ["Sequence[str]"], "Awaitable[set[UUID]]",
]


class TransientEventDelivery:
    """In-memory event delivery using asyncio Futures.

    Implements the ``EventDelivery`` protocol from ``orxtra.protocols``.
    Same semantics as the scheduler's ``EventRegistry``: fire resolves
    all current waiters, events fired before any waiter registers are
    silently lost (no replay), and multiple waiters on the same event
    all receive the same payload.
    """

    def __init__(self) -> None:
        self._listeners: dict[
            str, list[asyncio.Future[dict[str, object] | None]]
        ] = {}

    async def fire(
        self,
        event_name: str,
        payload: dict[str, object] | None = None,
        *,
        source: str | None = None,  # noqa: ARG002 -- EventDelivery protocol signature
    ) -> None:
        futures = self._listeners.pop(event_name, [])
        for fut in futures:
            if not fut.done():
                fut.set_result(payload)

    async def wait_for(
        self,
        event_name: str,
        *,
        deadline_seconds: float,
    ) -> dict[str, object] | None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, object] | None] = loop.create_future()
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=deadline_seconds)
        except TimeoutError:
            return None


async def match_subscription(
    event_type: str,
    principal_id: UUID | None,
    data: dict[str, Any] | None,  # noqa: ARG001 -- reserved for jsonb predicates
    filter_predicate: FilterPredicate,
    resolve_source_principals: SourcePrincipalResolver,
) -> bool:
    """Evaluate whether an event matches a subscription's filter.

    Filter semantics:
    - ``event_types``: if set, event_type must be in the list.
    - ``sources``: if set (a list of source slugs), the event's
      ``principal_id`` must be one of the source principals those slugs
      resolve to. Slugs are the authoring interface; principals are the runtime
      identity. The injected ``resolve_source_principals`` callback bridges the
      two so dispatch never touches identity storage directly.
    - ``data_predicates``: reserved for future jsonb matching; ignored now.
    - All None fields are treated as wildcards (match everything).
    """
    if (
        filter_predicate.event_types is not None
        and event_type not in filter_predicate.event_types
    ):
        return False
    # data_predicates: reserved, not evaluated yet.
    if filter_predicate.sources is None:
        return True
    allowed = await resolve_source_principals(filter_predicate.sources)
    return principal_id is not None and principal_id in allowed
