from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from orxtra.protocols import (
    KIND_SOURCE,
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
from uuid6 import uuid7

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxtra.protocols import Principal, PrincipalStorage


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
    caller_principal: Principal,
    filter_pred: FilterPredicate,
    actions: list[dict[str, Any]],
    *,
    storage: str = "persistent",
) -> UUID:
    """Create a subscription with actions, owned by the calling principal.

    Thin wrapper: builds a Subscription from the filter predicate, attributing
    ownership to ``caller_principal`` (the authenticated actor derived at the
    dispatch choke point), persists it via the backend, then creates
    SubscriptionActions for each action dict in order. Action dicts are resolved
    to typed Action instances before storage.

    A subscription is operational state owned by its principal: the FK CASCADEs,
    so deleting the owner deletes the subscription.
    """
    now = datetime.now(tz=UTC)
    sub = Subscription(
        id=uuid7(),
        filter=filter_pred,
        enabled=True,
        storage=storage,
        principal_id=caller_principal.id,
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
    principal_id: UUID | None = None,
) -> list[Subscription]:
    """List subscriptions, optionally filtering to enabled-only and by owner."""
    return await backend.list_subscriptions(
        enabled_only=enabled_only, principal_id=principal_id,
    )


# -- Source CRUD --


async def create_source(
    pool: asyncpg.Pool | None,
    backend: DispatchBackend,
    principal_storage: PrincipalStorage,
    caller_principal: Principal,
    slug: str,
    name: str,
    *,
    credential_id: UUID | None = None,
    config: dict[str, Any] | None = None,
) -> UUID:
    """Create a new event source, minting the source's identity at birth.

    Flow, mirroring the runs vertical:

    1. If a ``credential_id`` is supplied, validate it exists against auth's
       ``credentials`` table via ``pool`` -- an unknown id is a hard error. This
       closes the honor-system gap where a source could reference a
       non-existent credential. Reading auth's tables is legal: the
       single-writer rule bars *writes* to another module's tables, not reads.
    2. Generate the source id client-side so the source's principal can exist
       before the row that FKs into it.
    3. Mint the source principal (kind=source, external_ref=source id,
       display_name=slug).
    4. Persist the source attributed to the caller (``created_by``).
    """
    if credential_id is not None:
        if pool is None:
            msg = (
                "create_source requires a database pool to validate "
                f"credential_id {credential_id}"
            )
            raise ValueError(msg)
        # Cross-module read of auth's credentials table: permitted (reads only;
        # the single-writer rule bars writes to another module's tables).
        async with pool.acquire() as conn, conn.transaction():
            exists = await conn.fetchval(
                "SELECT EXISTS(SELECT 1 FROM credentials WHERE id = $1)",
                credential_id,
            )
        if not exists:
            msg = f"Unknown credential_id {credential_id}: no such credential"
            raise ValueError(msg)

    source_id = uuid7()
    await principal_storage.mint_principal(KIND_SOURCE, source_id, slug)
    now = datetime.now(tz=UTC)
    source = Source(
        id=source_id,
        slug=slug,
        name=name,
        credential_id=credential_id,
        config=config,
        created_by=caller_principal.id,
        created_at=now,
    )
    return await backend.create_source(source)


async def get_source(
    backend: DispatchBackend,
    source_id: UUID,
) -> Source | None:
    """Get a source by ID, or None if not found."""
    return await backend.get_source(source_id)


async def get_source_by_slug(
    backend: DispatchBackend,
    slug: str,
) -> Source | None:
    """Get a source by slug, or None if not found."""
    return await backend.get_source_by_slug(slug)


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
