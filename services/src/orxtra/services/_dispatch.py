from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from uuid6 import uuid7

from orxtra.protocols import (
    Action,
    DispatchBackend,
    EventAction,
    FilterPredicate,
    LogAction,
    ScriptAction,
    Source,
    Subscription,
    SubscriptionAction,
    WorkflowAction,
)

if TYPE_CHECKING:
    from uuid import UUID


def _resolve_action_from_dict(action_data: dict[str, Any]) -> Action:
    """Resolve a plain dict to a typed Action instance.

    Detects the action type from dict keys and validates via pydantic.
    """
    if "callable" in action_data:
        return ScriptAction.model_validate(action_data)
    if "message" in action_data:
        return LogAction.model_validate(action_data)
    if "workflow_path" in action_data:
        return WorkflowAction.model_validate(action_data)
    if "event_type" in action_data:
        return EventAction.model_validate(action_data)

    msg = f"Cannot determine action type from keys: {set(action_data.keys())}"
    raise ValueError(msg)


async def subscribe(
    backend: DispatchBackend,
    filter_pred: FilterPredicate,
    actions: list[dict[str, Any]],
    *,
    storage: str = "persistent",
    owner_run_id: UUID | None = None,
) -> UUID:
    """Create a subscription with actions.

    Thin wrapper: builds a Subscription from the filter predicate,
    persists it via the backend, then creates SubscriptionActions
    for each action dict in order. Action dicts are resolved to
    typed Action instances before storage.
    """
    now = datetime.now(tz=UTC)
    sub = Subscription(
        id=uuid7(),
        filter=filter_pred,
        enabled=True,
        storage=storage,
        owner_run_id=owner_run_id,
        created_at=now,
    )
    await backend.create_subscription(sub)

    for position, action_config in enumerate(actions):
        accumulator_config = action_config.pop("accumulator_config", None)
        action_data = action_config.pop("action", action_config)
        action = _resolve_action_from_dict(action_data)
        sub_action = SubscriptionAction(
            id=uuid7(),
            subscription_id=sub.id,
            position=position,
            action=action,
            accumulator_config=accumulator_config,
            created_at=now,
        )
        await backend.create_action(sub_action)

    return sub.id


async def unsubscribe(
    backend: DispatchBackend,
    subscription_id: UUID,
) -> None:
    """Disable and delete a subscription and its actions."""
    existing = await backend.get_subscription(subscription_id)
    if existing is None:
        msg = f"subscription {subscription_id} not found"
        raise ValueError(msg)
    await backend.delete_actions(subscription_id)
    await backend.delete_subscription(subscription_id)


async def list_subscriptions(
    backend: DispatchBackend,
    *,
    enabled_only: bool = True,
) -> list[Subscription]:
    """List subscriptions, optionally filtering to enabled only."""
    return await backend.list_subscriptions(enabled_only=enabled_only)


# -- Source CRUD --


async def create_source(
    backend: DispatchBackend,
    slug: str,
    name: str,
    *,
    auth_method: str | None = None,
    auth_config: dict[str, Any] | None = None,
) -> UUID:
    """Create a new event source."""
    now = datetime.now(tz=UTC)
    source = Source(
        id=uuid7(),
        slug=slug,
        name=name,
        auth_method=auth_method,
        auth_config=auth_config,
        created_at=now,
    )
    return await backend.create_source(source)


async def get_source(
    backend: DispatchBackend,
    source_id: UUID,
) -> Source | None:
    """Get a source by ID, or None if not found."""
    return await backend.get_source(source_id)


async def list_sources(
    backend: DispatchBackend,
) -> list[Source]:
    """List all registered sources."""
    return await backend.list_sources()


async def delete_source(
    backend: DispatchBackend,
    source_id: UUID,
) -> None:
    """Delete a source by ID."""
    await backend.delete_source(source_id)
