from __future__ import annotations

from datetime import UTC, datetime

import pytest
from orxtra.dispatch import FilterPredicate, InMemoryDispatchBackend
from orxtra.identity import InMemoryPrincipalStorage
from orxtra.protocols import KIND_CONSUMER, KIND_SOURCE, Principal
from orxtra.services._dispatch import (
    create_source,
    delete_source,
    get_source,
    get_source_by_slug,
    list_sources,
    list_subscriptions,
    subscribe,
    unsubscribe,
)
from uuid6 import uuid7


@pytest.fixture
def backend() -> InMemoryDispatchBackend:
    return InMemoryDispatchBackend()


@pytest.fixture
def storage() -> InMemoryPrincipalStorage:
    return InMemoryPrincipalStorage()


@pytest.fixture
def caller() -> Principal:
    """The caller principal whose id becomes each source's created_by."""
    return Principal(
        id=uuid7(),
        kind=KIND_CONSUMER,
        external_ref=uuid7(),
        display_name="test-caller",
        created_at=datetime.now(tz=UTC),
    )


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
async def test_create_source(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    source_id = await create_source(None, backend, storage, caller, "github", "GitHub")
    source = await backend.get_source(source_id)
    assert source is not None
    assert source.slug == "github"
    assert source.name == "GitHub"
    assert source.credential_id is None
    # The row is attributed to the caller.
    assert source.created_by == caller.id
    # The source's own principal was minted (kind=source, display_name=slug).
    source_principal = await storage.get_principal_by_ref(KIND_SOURCE, source_id)
    assert source_principal is not None
    assert source_principal.display_name == "github"


@pytest.mark.asyncio
async def test_create_source_credential_without_pool_raises(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    """A credential_id cannot be validated without a pool -- hard error."""
    with pytest.raises(ValueError, match="requires a database pool"):
        await create_source(
            None,
            backend,
            storage,
            caller,
            "webhook",
            "Webhook",
            credential_id=uuid7(),
        )


@pytest.mark.asyncio
async def test_create_source_duplicate_slug_raises(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    await create_source(None, backend, storage, caller, "github", "GitHub")
    with pytest.raises(ValueError, match="already exists"):
        await create_source(None, backend, storage, caller, "github", "GitHub 2")


# -- get_source --


@pytest.mark.asyncio
async def test_get_source(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    source_id = await create_source(None, backend, storage, caller, "gitlab", "GitLab")
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
async def test_list_sources(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    await create_source(None, backend, storage, caller, "a", "A")
    await create_source(None, backend, storage, caller, "b", "B")
    result = await list_sources(backend)
    assert len(result) == 2


# -- delete_source --


@pytest.mark.asyncio
async def test_delete_source(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    source_id = await create_source(None, backend, storage, caller, "temp", "Temp")
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


# -- create_source with config --


@pytest.mark.asyncio
async def test_create_source_with_config(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    """Source config is stored and retrievable."""
    cfg = {"event_type_path": "$.headers.X-Event-Type", "mapping": {"push": "git.push"}}
    source_id = await create_source(
        None, backend, storage, caller, "gh", "GitHub", config=cfg,
    )
    source = await backend.get_source(source_id)
    assert source is not None
    assert source.config == cfg


@pytest.mark.asyncio
async def test_create_source_config_none_by_default(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    """Config defaults to None when not provided."""
    source_id = await create_source(None, backend, storage, caller, "plain", "Plain")
    source = await backend.get_source(source_id)
    assert source is not None
    assert source.config is None


# -- get_source_by_slug --


@pytest.mark.asyncio
async def test_get_source_by_slug(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    await create_source(None, backend, storage, caller, "gitlab", "GitLab")
    source = await get_source_by_slug(backend, "gitlab")
    assert source is not None
    assert source.slug == "gitlab"
    assert source.name == "GitLab"


@pytest.mark.asyncio
async def test_get_source_by_slug_not_found(
    backend: InMemoryDispatchBackend,
) -> None:
    result = await get_source_by_slug(backend, "nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_get_source_by_slug_with_config(
    backend: InMemoryDispatchBackend,
    storage: InMemoryPrincipalStorage,
    caller: Principal,
) -> None:
    """get_source_by_slug preserves config."""
    cfg = {"event_type_field": "action"}
    await create_source(None, backend, storage, caller, "webhook", "Webhook", config=cfg)
    source = await get_source_by_slug(backend, "webhook")
    assert source is not None
    assert source.config == cfg
