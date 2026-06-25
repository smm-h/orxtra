from __future__ import annotations

import pytest

from orxtra.dispatch import FilterPredicate, InMemoryDispatchBackend
from orxtra.services._dispatch import (
    create_source,
    delete_source,
    get_source,
    list_sources,
    list_subscriptions,
    subscribe,
    unsubscribe,
)


@pytest.fixture
def backend() -> InMemoryDispatchBackend:
    return InMemoryDispatchBackend()


@pytest.fixture
def sample_filter() -> FilterPredicate:
    return FilterPredicate(event_types=["task_completed"])


# -- subscribe --


@pytest.mark.asyncio
async def test_subscribe_creates_subscription(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend,
        sample_filter,
        [{"action": {"callable": "mod:func"}}],
    )
    sub = await backend.get_subscription(sub_id)
    assert sub is not None
    assert sub.enabled is True
    assert sub.filter == sample_filter


@pytest.mark.asyncio
async def test_subscribe_creates_actions(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend,
        sample_filter,
        [
            {"action": {"callable": "mod:func_a"}},
            {"action": {"message": "hello", "level": "info"}},
        ],
    )
    actions = await backend.list_actions(sub_id)
    assert len(actions) == 2
    assert actions[0].position == 0
    assert actions[1].position == 1


@pytest.mark.asyncio
async def test_subscribe_with_accumulator_config(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend,
        sample_filter,
        [
            {
                "action": {"callable": "mod:func"},
                "accumulator_config": {"threshold": 10},
            },
        ],
    )
    actions = await backend.list_actions(sub_id)
    assert len(actions) == 1
    assert actions[0].accumulator_config == {"threshold": 10}


@pytest.mark.asyncio
async def test_subscribe_with_owner_run_id(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    from uuid import UUID

    run_id = UUID("12345678-1234-1234-1234-123456789abc")
    sub_id = await subscribe(
        backend,
        sample_filter,
        [{"action": {"callable": "mod:func"}}],
        owner_run_id=run_id,
    )
    sub = await backend.get_subscription(sub_id)
    assert sub is not None
    assert sub.owner_run_id == run_id


@pytest.mark.asyncio
async def test_subscribe_transient_storage(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend,
        sample_filter,
        [{"action": {"callable": "mod:func"}}],
        storage="transient",
    )
    sub = await backend.get_subscription(sub_id)
    assert sub is not None
    assert sub.storage == "transient"


# -- unsubscribe --


@pytest.mark.asyncio
async def test_unsubscribe_removes_subscription(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend,
        sample_filter,
        [{"action": {"callable": "mod:func"}}],
    )
    await unsubscribe(backend, sub_id)

    sub = await backend.get_subscription(sub_id)
    assert sub is None


@pytest.mark.asyncio
async def test_unsubscribe_removes_actions(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend,
        sample_filter,
        [
            {"action": {"callable": "mod:func_a"}},
            {"action": {"callable": "mod:func_b"}},
        ],
    )
    await unsubscribe(backend, sub_id)

    actions = await backend.list_actions(sub_id)
    assert actions == []


@pytest.mark.asyncio
async def test_unsubscribe_not_found(
    backend: InMemoryDispatchBackend,
) -> None:
    from uuid import uuid4

    with pytest.raises(ValueError, match="not found"):
        await unsubscribe(backend, uuid4())


# -- list_subscriptions --


@pytest.mark.asyncio
async def test_list_subscriptions_empty(
    backend: InMemoryDispatchBackend,
) -> None:
    result = await list_subscriptions(backend)
    assert result == []


@pytest.mark.asyncio
async def test_list_subscriptions_returns_enabled(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    await subscribe(backend, sample_filter, [{"action": {"callable": "mod:func"}}])
    await subscribe(backend, sample_filter, [{"action": {"callable": "mod:func2"}}])

    result = await list_subscriptions(backend, enabled_only=True)
    assert len(result) == 2


@pytest.mark.asyncio
async def test_list_subscriptions_includes_disabled(
    backend: InMemoryDispatchBackend,
    sample_filter: FilterPredicate,
) -> None:
    sub_id = await subscribe(
        backend, sample_filter, [{"action": {"callable": "mod:func"}}],
    )
    # Disable via backend directly.
    await backend.update_subscription(sub_id, enabled=False)

    enabled = await list_subscriptions(backend, enabled_only=True)
    assert len(enabled) == 0

    all_subs = await list_subscriptions(backend, enabled_only=False)
    assert len(all_subs) == 1


# -- create_source --


@pytest.mark.asyncio
async def test_create_source(backend: InMemoryDispatchBackend) -> None:
    source_id = await create_source(backend, "github", "GitHub")
    source = await backend.get_source(source_id)
    assert source is not None
    assert source.slug == "github"
    assert source.name == "GitHub"
    assert source.auth_method is None
    assert source.auth_config is None


@pytest.mark.asyncio
async def test_create_source_with_auth(backend: InMemoryDispatchBackend) -> None:
    source_id = await create_source(
        backend,
        "webhook",
        "Webhook",
        auth_method="hmac",
        auth_config={"secret": "s3cret"},
    )
    source = await backend.get_source(source_id)
    assert source is not None
    assert source.auth_method == "hmac"
    assert source.auth_config == {"secret": "s3cret"}


@pytest.mark.asyncio
async def test_create_source_duplicate_slug_raises(
    backend: InMemoryDispatchBackend,
) -> None:
    await create_source(backend, "github", "GitHub")
    with pytest.raises(ValueError, match="already exists"):
        await create_source(backend, "github", "GitHub 2")


# -- get_source --


@pytest.mark.asyncio
async def test_get_source(backend: InMemoryDispatchBackend) -> None:
    source_id = await create_source(backend, "gitlab", "GitLab")
    source = await get_source(backend, source_id)
    assert source is not None
    assert source.slug == "gitlab"


@pytest.mark.asyncio
async def test_get_source_not_found(backend: InMemoryDispatchBackend) -> None:
    from uuid import uuid4

    result = await get_source(backend, uuid4())
    assert result is None


# -- list_sources --


@pytest.mark.asyncio
async def test_list_sources_empty(backend: InMemoryDispatchBackend) -> None:
    result = await list_sources(backend)
    assert result == []


@pytest.mark.asyncio
async def test_list_sources(backend: InMemoryDispatchBackend) -> None:
    await create_source(backend, "a", "A")
    await create_source(backend, "b", "B")
    result = await list_sources(backend)
    assert len(result) == 2


# -- delete_source --


@pytest.mark.asyncio
async def test_delete_source(backend: InMemoryDispatchBackend) -> None:
    source_id = await create_source(backend, "temp", "Temp")
    await delete_source(backend, source_id)
    result = await get_source(backend, source_id)
    assert result is None


@pytest.mark.asyncio
async def test_delete_source_nonexistent_noop(
    backend: InMemoryDispatchBackend,
) -> None:
    from uuid import uuid4

    # Should not raise.
    await delete_source(backend, uuid4())
