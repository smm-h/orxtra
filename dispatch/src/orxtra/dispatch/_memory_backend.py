from __future__ import annotations

from typing import Any
from uuid import UUID

from orxtra.dispatch._types import (
    AccumulatorEntry,
    Source,
    Subscription,
    SubscriptionAction,
)


class InMemoryDispatchBackend:
    """Dict-based in-memory implementation of DispatchBackend."""

    def __init__(self) -> None:
        self._sources: dict[UUID, Source] = {}
        self._subscriptions: dict[UUID, Subscription] = {}
        self._actions: dict[UUID, SubscriptionAction] = {}
        self._accumulator: dict[UUID, AccumulatorEntry] = {}
        self._claimed: set[UUID] = set()
        self._cursors: dict[str, UUID] = {}
        self._completions: set[tuple[UUID, UUID]] = set()
        self._events: list[dict[str, Any]] = []

    # -- SourceStorage --

    async def create_source(self, source: Source) -> UUID:
        for existing in self._sources.values():
            if existing.slug == source.slug:
                msg = f"Source with slug {source.slug!r} already exists"
                raise ValueError(msg)
        self._sources[source.id] = source
        return source.id

    async def get_source(self, source_id: UUID) -> Source | None:
        return self._sources.get(source_id)

    async def get_source_by_slug(self, slug: str) -> Source | None:
        for source in self._sources.values():
            if source.slug == slug:
                return source
        return None

    async def list_sources(self) -> list[Source]:
        return list(self._sources.values())

    async def delete_source(self, source_id: UUID) -> None:
        self._sources.pop(source_id, None)

    # -- SubscriptionStorage --

    async def create_subscription(self, subscription: Subscription) -> UUID:
        self._subscriptions[subscription.id] = subscription
        return subscription.id

    async def get_subscription(self, sub_id: UUID) -> Subscription | None:
        return self._subscriptions.get(sub_id)

    async def list_subscriptions(
        self, *, enabled_only: bool = True,
    ) -> list[Subscription]:
        subs = list(self._subscriptions.values())
        if enabled_only:
            subs = [s for s in subs if s.enabled]
        return subs

    async def update_subscription(
        self, sub_id: UUID, *, enabled: bool,
    ) -> None:
        existing = self._subscriptions.get(sub_id)
        if existing is None:
            msg = f"Subscription {sub_id} not found"
            raise KeyError(msg)
        # Frozen model -- rebuild with updated field.
        self._subscriptions[sub_id] = existing.model_copy(update={"enabled": enabled})

    async def delete_subscription(self, sub_id: UUID) -> None:
        self._subscriptions.pop(sub_id, None)

    # -- ActionStorage --

    async def create_action(self, action: SubscriptionAction) -> UUID:
        self._actions[action.id] = action
        return action.id

    async def list_actions(self, sub_id: UUID) -> list[SubscriptionAction]:
        return sorted(
            (a for a in self._actions.values() if a.subscription_id == sub_id),
            key=lambda a: a.position,
        )

    async def delete_actions(self, sub_id: UUID) -> None:
        to_remove = [
            aid for aid, a in self._actions.items() if a.subscription_id == sub_id
        ]
        for aid in to_remove:
            self._actions.pop(aid)

    # -- AccumulatorStorage --

    async def buffer_event(self, entry: AccumulatorEntry) -> UUID:
        self._accumulator[entry.id] = entry
        return entry.id

    async def claim_batch(
        self, action_id: UUID, limit: int = 100,
    ) -> list[AccumulatorEntry]:
        unclaimed = [
            e
            for e in self._accumulator.values()
            if e.subscription_action_id == action_id and e.id not in self._claimed
        ]
        # Oldest first.
        unclaimed.sort(key=lambda e: e.created_at)
        batch = unclaimed[:limit]
        for e in batch:
            self._claimed.add(e.id)
        return batch

    async def confirm_batch(self, entry_ids: list[UUID]) -> None:
        for eid in entry_ids:
            self._accumulator.pop(eid, None)
            self._claimed.discard(eid)

    async def pending_count(self, action_id: UUID) -> int:
        return sum(
            1
            for e in self._accumulator.values()
            if e.subscription_action_id == action_id
        )

    # -- CursorStorage --

    async def get_cursor_position(
        self, cursor_name: str,
    ) -> UUID | None:
        return self._cursors.get(cursor_name)

    async def advance_cursor(
        self, cursor_name: str, event_id: UUID,
    ) -> None:
        self._cursors[cursor_name] = event_id

    # -- CompletionStorage --

    async def is_action_completed(
        self, event_id: UUID, action_id: UUID,
    ) -> bool:
        return (event_id, action_id) in self._completions

    async def record_completion(
        self,
        event_id: UUID,
        action_id: UUID,
        result_status: str,  # noqa: ARG002 -- DispatchBackend protocol signature
    ) -> None:
        self._completions.add((event_id, action_id))

    # -- Event polling --

    async def poll_events_since(
        self,
        since_id: UUID | None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        if since_id is None:
            return self._events[:limit]
        found = False
        result: list[dict[str, Any]] = []
        for ev in self._events:
            if found:
                result.append(ev)
                if len(result) >= limit:
                    break
            elif ev["id"] == since_id:
                found = True
        return result

    def inject_event(self, event: dict[str, Any]) -> None:
        """Test helper: add an event to the in-memory store."""
        self._events.append(event)
