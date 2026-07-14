"""Principal CRUD service functions.

Thin wrappers over ``PrincipalStorage`` that add the service-layer policy
the storage deliberately omits: kind validation (via ``KindRegistry``) and
the refusal to delete the singleton system principal. Storage accepts any
string kind and deletes any row; the enforcement point lives here.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from orxtra.protocols import (
    KIND_RUN,
    KIND_SOURCE,
    KIND_SYSTEM,
    FilterPredicate,
    NotifyAction,
    Subscription,
    SubscriptionAction,
)
from uuid6 import uuid7

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.identity import KindRegistry
    from orxtra.protocols import DispatchBackend, Principal, PrincipalStorage

# Kinds that are infrastructure-level identities and never receive
# self-subscriptions. Consumer and app-registered kinds DO.
_NO_SELF_SUBSCRIPTION_KINDS: frozenset[str] = frozenset({
    KIND_RUN,
    KIND_SOURCE,
    KIND_SYSTEM,
})


async def create_principal(
    dispatch_backend: DispatchBackend,
    principal_storage: PrincipalStorage,
    kind_registry: KindRegistry,
    *,
    kind: str,
    external_ref: UUID,
    display_name: str | None = None,
    notification_event_types: list[str] | None = None,
) -> Principal:
    """Validate the kind, then idempotently mint the principal.

    The service layer is the kind-enforcement point: ``kind_registry.validate``
    hard-errors on an unregistered kind before any row is written (storage
    itself accepts any string). Minting is idempotent on ``(kind,
    external_ref)`` -- a retry after a partial failure, or a second call with
    the same reference, returns the existing row rather than creating a
    duplicate.

    ``kind == "system"`` is rejected outright: the system principal is a
    seeded singleton, not something the API mints. Allowing it would let a
    caller create a SECOND system-kind row under an arbitrary external_ref --
    a row the delete path refuses to remove, leaving it permanently stuck.

    For consumer and app-registered kinds, ``notification_event_types`` is
    required and triggers automatic self-subscription creation: the minted
    principal subscribes to events matching the given types with its own id
    as both the filter's principal_id and the NotifyAction target. For
    infrastructure kinds (run, source, system), self-subscriptions are
    rejected.
    """
    if kind == KIND_SYSTEM:
        msg = (
            "Refusing to create a system principal. The system principal is a "
            "seeded singleton and cannot be minted via the API -- it is "
            "created once during database seeding."
        )
        raise ValueError(msg)
    kind_registry.validate(kind)

    # Validate notification_event_types before minting.
    if kind in _NO_SELF_SUBSCRIPTION_KINDS:
        if notification_event_types is not None:
            msg = (
                f"{kind} principals do not support self-subscriptions"
            )
            raise ValueError(msg)
    elif notification_event_types is None:
        # Consumer and app-registered kinds require event types.
        msg = (
            f"{kind} principals must specify notification_event_types"
        )
        raise ValueError(msg)

    principal = await principal_storage.mint_principal(
        kind, external_ref, display_name,
    )

    # Create self-subscription for consumer and app-registered kinds.
    if notification_event_types is not None:
        await _create_self_subscription(
            dispatch_backend, principal, notification_event_types,
        )

    return principal


async def _create_self_subscription(
    backend: DispatchBackend,
    principal: Principal,
    event_types: list[str],
) -> None:
    """Create a self-subscription for a newly minted principal.

    The subscription filters events by the given event types AND the
    principal's own id, delivering a NotifyAction back to the same
    principal. This is the mechanism by which consumers (and app-registered
    kinds) automatically receive notifications for events they care about.
    """
    now = datetime.now(tz=UTC)
    filter_pred = FilterPredicate(
        event_types=event_types,
        principal_id=principal.id,
    )
    sub = Subscription(
        id=uuid7(),
        filter=filter_pred,
        enabled=True,
        storage="persistent",
        principal_id=principal.id,
        created_at=now,
    )
    await backend.create_subscription(sub)

    action = NotifyAction(
        target_principal_id=principal.id,
        source_ref="self-subscription",
        payload={},
    )
    sub_action = SubscriptionAction(
        id=uuid7(),
        subscription_id=sub.id,
        position=0,
        action=action,
        accumulator_config=None,
        created_at=now,
    )
    await backend.create_action(sub_action)


async def get_principal(
    principal_storage: PrincipalStorage,
    *,
    principal_id: UUID,
) -> Principal | None:
    """Fetch a principal by id, or ``None`` if it does not exist."""
    return await principal_storage.get_principal(principal_id)


async def list_principals(
    principal_storage: PrincipalStorage,
    *,
    kind: str | None = None,
) -> list[Principal]:
    """List principals, optionally filtered by kind."""
    return await principal_storage.list_principals(kind)


async def delete_principal(
    principal_storage: PrincipalStorage,
    *,
    principal_id: UUID,
) -> None:
    """Delete a principal, refusing to delete the system principal.

    The singleton system principal anchors framework-owned attribution and
    must never be removed, so it is fetched first and a ``kind == "system"``
    match is a hard error. Any other principal is delegated to storage, where
    a ``PrincipalInUseError`` propagates if the row is still referenced.
    """
    existing = await principal_storage.get_principal(principal_id)
    if existing is not None and existing.kind == KIND_SYSTEM:
        msg = (
            f"Refusing to delete the system principal {principal_id}. The "
            f"system principal is a framework-owned singleton and is never "
            f"deletable."
        )
        raise ValueError(msg)
    await principal_storage.delete_principal(principal_id)


# -- Recovery -----------------------------------------------------------------

# Conservative window for the orphan-principal sweep. A concurrent start_run
# mints the run principal first, then inserts the runs row. Five minutes is
# far longer than any plausible gap between the two statements -- anything
# older than this without a matching runs row is a crash orphan.
_ORPHAN_SWEEP_AGE = timedelta(minutes=5)


async def sweep_orphaned_run_principals(
    principal_storage: PrincipalStorage,
) -> int:
    """Sweep kind=run principals with no matching ``runs`` row.

    Delegates to storage with a conservative age guard to avoid racing
    with a concurrent ``start_run`` that has minted the principal but
    not yet created the runs row.
    """
    return await principal_storage.sweep_orphaned_run_principals(
        _ORPHAN_SWEEP_AGE,
    )
