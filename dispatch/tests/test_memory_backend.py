from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from uuid6 import uuid7

from orxtra.dispatch import (
    AccumulatorEntry,
    FilterPredicate,
    InMemoryDispatchBackend,
    Source,
    Subscription,
    SubscriptionAction,
)
from orxtra.protocols import DispatchBackend, ScriptAction

NOW = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)
LATER = datetime(2025, 7, 1, 12, 5, 0, tzinfo=UTC)


def _sub(
    *,
    sub_id: UUID | None = None,
    enabled: bool = True,
    event_types: list[str] | None = None,
) -> Subscription:
    return Subscription(
        id=sub_id or uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=enabled,
        created_at=NOW,
    )


def _action(
    *,
    action_id: UUID | None = None,
    sub_id: UUID,
    position: int = 0,
) -> SubscriptionAction:
    return SubscriptionAction(
        id=action_id or uuid7(),
        subscription_id=sub_id,
        position=position,
        action=ScriptAction(callable="my.module:handler"),
        created_at=NOW,
    )


def _source(
    *,
    source_id: UUID | None = None,
    slug: str = "github",
    name: str = "GitHub",
    auth_method: str | None = None,
    auth_config: dict[str, object] | None = None,
) -> Source:
    return Source(
        id=source_id or uuid7(),
        slug=slug,
        name=name,
        auth_method=auth_method,
        auth_config=auth_config,
        created_at=NOW,
    )


def _accum(
    *,
    entry_id: UUID | None = None,
    action_id: UUID,
    event_id: UUID | None = None,
    created_at: datetime = NOW,
) -> AccumulatorEntry:
    return AccumulatorEntry(
        id=entry_id or uuid7(),
        subscription_action_id=action_id,
        event_id=event_id or uuid7(),
        created_at=created_at,
    )


# -- Subscription CRUD --


class TestSubscriptionCRUD:
    @pytest.mark.asyncio
    async def test_create_and_get(self, backend: InMemoryDispatchBackend) -> None:
        sub = _sub()
        result_id = await backend.create_subscription(sub)
        assert result_id == sub.id

        fetched = await backend.get_subscription(sub.id)
        assert fetched is not None
        assert fetched.id == sub.id
        assert fetched.enabled is True

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, backend: InMemoryDispatchBackend) -> None:
        result = await backend.get_subscription(uuid7())
        assert result is None

    @pytest.mark.asyncio
    async def test_list_enabled_only(self, backend: InMemoryDispatchBackend) -> None:
        enabled = _sub(enabled=True)
        disabled = _sub(enabled=False)
        await backend.create_subscription(enabled)
        await backend.create_subscription(disabled)

        result = await backend.list_subscriptions(enabled_only=True)
        assert len(result) == 1
        assert result[0].id == enabled.id

    @pytest.mark.asyncio
    async def test_list_all(self, backend: InMemoryDispatchBackend) -> None:
        await backend.create_subscription(_sub(enabled=True))
        await backend.create_subscription(_sub(enabled=False))

        result = await backend.list_subscriptions(enabled_only=False)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_update_enabled(self, backend: InMemoryDispatchBackend) -> None:
        sub = _sub(enabled=True)
        await backend.create_subscription(sub)

        await backend.update_subscription(sub.id, enabled=False)
        fetched = await backend.get_subscription(sub.id)
        assert fetched is not None
        assert fetched.enabled is False

    @pytest.mark.asyncio
    async def test_update_nonexistent_raises(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        with pytest.raises(KeyError):
            await backend.update_subscription(uuid7(), enabled=False)

    @pytest.mark.asyncio
    async def test_delete(self, backend: InMemoryDispatchBackend) -> None:
        sub = _sub()
        await backend.create_subscription(sub)
        await backend.delete_subscription(sub.id)

        result = await backend.get_subscription(sub.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        # Should not raise.
        await backend.delete_subscription(uuid7())


# -- Action CRUD --


class TestActionCRUD:
    @pytest.mark.asyncio
    async def test_create_and_list(self, backend: InMemoryDispatchBackend) -> None:
        sub_id = uuid7()
        a1 = _action(sub_id=sub_id, position=1)
        a0 = _action(sub_id=sub_id, position=0)
        await backend.create_action(a1)
        await backend.create_action(a0)

        actions = await backend.list_actions(sub_id)
        assert len(actions) == 2
        # Sorted by position.
        assert actions[0].position == 0
        assert actions[1].position == 1

    @pytest.mark.asyncio
    async def test_list_filters_by_subscription(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        sub1 = uuid7()
        sub2 = uuid7()
        await backend.create_action(_action(sub_id=sub1, position=0))
        await backend.create_action(_action(sub_id=sub2, position=0))

        actions = await backend.list_actions(sub1)
        assert len(actions) == 1
        assert actions[0].subscription_id == sub1

    @pytest.mark.asyncio
    async def test_delete_actions_by_subscription(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        sub_id = uuid7()
        other_sub = uuid7()
        await backend.create_action(_action(sub_id=sub_id, position=0))
        await backend.create_action(_action(sub_id=sub_id, position=1))
        await backend.create_action(_action(sub_id=other_sub, position=0))

        await backend.delete_actions(sub_id)

        assert await backend.list_actions(sub_id) == []
        assert len(await backend.list_actions(other_sub)) == 1

    @pytest.mark.asyncio
    async def test_list_empty(self, backend: InMemoryDispatchBackend) -> None:
        result = await backend.list_actions(uuid7())
        assert result == []


# -- Accumulator lifecycle --


class TestAccumulatorLifecycle:
    @pytest.mark.asyncio
    async def test_buffer_and_pending_count(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        action_id = uuid7()
        e1 = _accum(action_id=action_id)
        e2 = _accum(action_id=action_id)
        await backend.buffer_event(e1)
        await backend.buffer_event(e2)

        count = await backend.pending_count(action_id)
        assert count == 2

    @pytest.mark.asyncio
    async def test_claim_batch(self, backend: InMemoryDispatchBackend) -> None:
        action_id = uuid7()
        e1 = _accum(action_id=action_id, created_at=NOW)
        e2 = _accum(action_id=action_id, created_at=LATER)
        await backend.buffer_event(e2)
        await backend.buffer_event(e1)

        batch = await backend.claim_batch(action_id, limit=10)
        assert len(batch) == 2
        # Oldest first.
        assert batch[0].id == e1.id
        assert batch[1].id == e2.id

    @pytest.mark.asyncio
    async def test_claim_batch_respects_limit(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        action_id = uuid7()
        for _ in range(5):
            await backend.buffer_event(_accum(action_id=action_id))

        batch = await backend.claim_batch(action_id, limit=3)
        assert len(batch) == 3

    @pytest.mark.asyncio
    async def test_claim_batch_filters_by_action(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        a1 = uuid7()
        a2 = uuid7()
        await backend.buffer_event(_accum(action_id=a1))
        await backend.buffer_event(_accum(action_id=a2))

        batch = await backend.claim_batch(a1)
        assert len(batch) == 1
        assert batch[0].subscription_action_id == a1

    @pytest.mark.asyncio
    async def test_confirm_batch_removes_entries(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        action_id = uuid7()
        e1 = _accum(action_id=action_id)
        e2 = _accum(action_id=action_id)
        await backend.buffer_event(e1)
        await backend.buffer_event(e2)

        batch = await backend.claim_batch(action_id)
        await backend.confirm_batch([e.id for e in batch])

        assert await backend.pending_count(action_id) == 0

    @pytest.mark.asyncio
    async def test_confirm_partial_batch(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        action_id = uuid7()
        e1 = _accum(action_id=action_id)
        e2 = _accum(action_id=action_id)
        await backend.buffer_event(e1)
        await backend.buffer_event(e2)

        await backend.claim_batch(action_id)
        await backend.confirm_batch([e1.id])

        assert await backend.pending_count(action_id) == 1

    @pytest.mark.asyncio
    async def test_claim_empty(self, backend: InMemoryDispatchBackend) -> None:
        batch = await backend.claim_batch(uuid7())
        assert batch == []

    @pytest.mark.asyncio
    async def test_confirm_nonexistent_is_noop(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        # Should not raise.
        await backend.confirm_batch([uuid7()])

    @pytest.mark.asyncio
    async def test_double_claim_does_not_return_claimed(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        action_id = uuid7()
        e1 = _accum(action_id=action_id)
        await backend.buffer_event(e1)

        batch1 = await backend.claim_batch(action_id)
        assert len(batch1) == 1

        batch2 = await backend.claim_batch(action_id)
        assert batch2 == []

    @pytest.mark.asyncio
    async def test_pending_count_includes_claimed(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        action_id = uuid7()
        await backend.buffer_event(_accum(action_id=action_id))

        await backend.claim_batch(action_id)
        # Claimed but not confirmed -- still pending.
        assert await backend.pending_count(action_id) == 1

    @pytest.mark.asyncio
    async def test_pending_count_empty(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        assert await backend.pending_count(uuid7()) == 0


# -- Source CRUD --


class TestSourceCRUD:
    @pytest.mark.asyncio
    async def test_create_and_get(self, backend: InMemoryDispatchBackend) -> None:
        src = _source()
        result_id = await backend.create_source(src)
        assert result_id == src.id

        fetched = await backend.get_source(src.id)
        assert fetched is not None
        assert fetched.id == src.id
        assert fetched.slug == "github"
        assert fetched.name == "GitHub"

    @pytest.mark.asyncio
    async def test_get_nonexistent(self, backend: InMemoryDispatchBackend) -> None:
        result = await backend.get_source(uuid7())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_slug(self, backend: InMemoryDispatchBackend) -> None:
        src = _source(slug="webhook-a")
        await backend.create_source(src)

        fetched = await backend.get_source_by_slug("webhook-a")
        assert fetched is not None
        assert fetched.id == src.id

        missing = await backend.get_source_by_slug("nonexistent")
        assert missing is None

    @pytest.mark.asyncio
    async def test_list(self, backend: InMemoryDispatchBackend) -> None:
        await backend.create_source(_source(slug="a", name="A"))
        await backend.create_source(_source(slug="b", name="B"))

        sources = await backend.list_sources()
        assert len(sources) == 2

    @pytest.mark.asyncio
    async def test_delete(self, backend: InMemoryDispatchBackend) -> None:
        src = _source()
        await backend.create_source(src)
        await backend.delete_source(src.id)

        result = await backend.get_source(src.id)
        assert result is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_is_noop(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        # Should not raise.
        await backend.delete_source(uuid7())

    @pytest.mark.asyncio
    async def test_duplicate_slug_raises(
        self, backend: InMemoryDispatchBackend,
    ) -> None:
        await backend.create_source(_source(slug="dup"))
        with pytest.raises(ValueError, match="already exists"):
            await backend.create_source(_source(slug="dup"))


# -- DispatchBackend protocol conformance --


class TestDispatchBackendProtocol:
    def test_isinstance_check(self) -> None:
        assert isinstance(InMemoryDispatchBackend(), DispatchBackend)
