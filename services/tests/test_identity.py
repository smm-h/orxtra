"""End-to-end tests for the principal CRUD capabilities.

Each capability is exercised through the generic dispatcher with a real
``InMemoryPrincipalStorage`` and ``KindRegistry``, verifying the service-layer
policy: kind validation, idempotent minting, system-principal delete refusal,
``PrincipalInUseError`` propagation, and self-subscription creation for
consumer and app-registered kinds.
"""

from __future__ import annotations

import importlib.util as _ilu
from pathlib import Path
from uuid import UUID

import pytest
from orxtra.dispatch import InMemoryDispatchBackend
from orxtra.identity import (
    InMemoryPrincipalStorage,
    KindRegistry,
    PrincipalInUseError,
)
from orxtra.protocols import KIND_SYSTEM, NotifyAction, Principal
from orxtra.services import DispatchContext, dispatch

_spec = _ilu.spec_from_file_location(
    "tests.shared_mocks",
    Path(__file__).resolve().parents[2] / "tests" / "shared_mocks.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
make_auth_context = _mod.make_auth_context

_APP_KIND = "user"
_EXT_REF = "11111111-1111-1111-1111-111111111111"
# Default event types for tests that just need a valid principal, not
# exercising self-subscription logic.
_DEFAULT_EVENT_TYPES = ["run_completed"]


def _context(
    storage: InMemoryPrincipalStorage,
    *,
    app_kinds: tuple[str, ...] = (_APP_KIND,),
    dispatch_backend: InMemoryDispatchBackend | None = None,
) -> DispatchContext:
    return DispatchContext(
        principal_storage=storage,
        kind_registry=KindRegistry(app_kinds),
        dispatch_backend=dispatch_backend or InMemoryDispatchBackend(),
        auth_context=make_auth_context(),
    )


async def test_create_principal_validates_known_kind() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    principal = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "display_name": "Ann",
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )

    assert isinstance(principal, Principal)
    assert principal.kind == _APP_KIND
    assert principal.external_ref == UUID(_EXT_REF)
    assert principal.display_name == "Ann"


async def test_create_principal_rejects_system_kind() -> None:
    storage = InMemoryPrincipalStorage()
    # KIND_SYSTEM is a registered builtin kind (always present in the
    # registry), so registry validation would accept it -- the create service
    # must reject it independently, before any row is minted, to prevent a
    # second undeletable system principal.
    ctx = _context(storage)

    with pytest.raises(ValueError, match="Refusing to create a system principal"):
        await dispatch(
            ctx,
            "create_principal",
            {"kind": KIND_SYSTEM, "external_ref": _EXT_REF},
        )

    # Nothing was written -- the rejection happens before minting.
    assert await storage.list_principals() == []


async def test_create_principal_rejects_unknown_kind() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    with pytest.raises(ValueError, match="Unknown principal kind"):
        await dispatch(
            ctx,
            "create_principal",
            {"kind": "bogus", "external_ref": _EXT_REF},
        )

    # Nothing was written -- validation happens before minting.
    assert await storage.list_principals() == []


async def test_create_principal_is_idempotent_returns_existing() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    first = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "display_name": "Ann",
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )
    second = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "display_name": "Ann",
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )

    assert first.id == second.id
    assert len(await storage.list_principals()) == 1


async def test_get_principal_round_trip() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    created = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )
    fetched = await dispatch(
        ctx,
        "get_principal",
        {"principal_id": str(created.id)},
    )

    assert fetched == created


async def test_get_principal_missing_returns_none() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    result = await dispatch(
        ctx,
        "get_principal",
        {"principal_id": "22222222-2222-2222-2222-222222222222"},
    )

    assert result is None


async def test_list_principals_round_trip_and_kind_filter() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )
    await dispatch(
        ctx,
        "create_principal",
        {
            "kind": "consumer",
            "external_ref": "33333333-3333-3333-3333-333333333333",
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )

    all_principals = await dispatch(ctx, "list_principals", {})
    assert len(all_principals) == 2

    users = await dispatch(ctx, "list_principals", {"kind": _APP_KIND})
    assert len(users) == 1
    assert users[0].kind == _APP_KIND


async def test_delete_principal_round_trip() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    created = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )
    await dispatch(ctx, "delete_principal", {"principal_id": str(created.id)})

    assert await storage.get_principal(created.id) is None


async def test_delete_principal_refuses_system_principal() -> None:
    storage = InMemoryPrincipalStorage()
    system = await storage.mint_principal(KIND_SYSTEM, UUID(int=0), "system")
    ctx = _context(storage)

    with pytest.raises(ValueError, match="Refusing to delete the system principal"):
        await dispatch(ctx, "delete_principal", {"principal_id": str(system.id)})

    # The system principal survives the refusal.
    assert await storage.get_principal(system.id) is not None


class _InUseStorage(InMemoryPrincipalStorage):
    """Storage stand-in whose delete always raises ``PrincipalInUseError``.

    Simulates the PG backend translating a RESTRICT foreign-key violation on
    a referenced principal, which the in-memory backend cannot exercise.
    """

    async def delete_principal(self, principal_id: UUID) -> None:
        raise PrincipalInUseError(principal_id)


async def test_delete_principal_propagates_in_use_error() -> None:
    storage = _InUseStorage()
    ctx = _context(storage)

    created = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "notification_event_types": _DEFAULT_EVENT_TYPES,
        },
    )

    with pytest.raises(PrincipalInUseError):
        await dispatch(ctx, "delete_principal", {"principal_id": str(created.id)})


# ---------------------------------------------------------------------------
# Service-level sweep
# ---------------------------------------------------------------------------


async def test_sweep_orphaned_run_principals_delegates_to_storage() -> None:
    """The service function delegates to storage.sweep_orphaned_run_principals
    with a 5-minute age guard."""
    from datetime import timedelta
    from unittest.mock import AsyncMock

    from orxtra.services._identity import sweep_orphaned_run_principals

    mock_storage = AsyncMock()
    mock_storage.sweep_orphaned_run_principals = AsyncMock(return_value=3)

    result = await sweep_orphaned_run_principals(mock_storage)

    assert result == 3
    mock_storage.sweep_orphaned_run_principals.assert_called_once_with(
        timedelta(minutes=5),
    )


# ---------------------------------------------------------------------------
# Self-subscription on create_principal
# ---------------------------------------------------------------------------

_CONSUMER_EVENT_TYPES = [
    "run_completed", "run_failed", "task_failed", "inbox_answered",
]


async def test_create_consumer_principal_creates_self_subscription() -> None:
    """Consumer principal with event_types creates a self-subscription."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    principal = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": "consumer",
            "external_ref": _EXT_REF,
            "display_name": "Bot",
            "notification_event_types": _CONSUMER_EVENT_TYPES,
        },
    )

    assert isinstance(principal, Principal)
    assert principal.kind == "consumer"

    # A subscription was created, owned by the minted principal.
    subs = await backend.list_subscriptions(
        enabled_only=True, principal_id=principal.id,
    )
    assert len(subs) == 1
    sub = subs[0]
    assert sub.principal_id == principal.id
    assert sub.filter.event_types == _CONSUMER_EVENT_TYPES
    assert sub.filter.principal_id == principal.id

    # A NotifyAction was created on that subscription.
    actions = await backend.list_actions(sub.id)
    assert len(actions) == 1
    action = actions[0]
    assert isinstance(action.action, NotifyAction)
    assert action.action.target_principal_id == principal.id
    assert action.action.source_ref == "self-subscription"


async def test_create_app_kind_principal_creates_self_subscription() -> None:
    """App-registered kinds also get self-subscriptions."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    principal = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": _APP_KIND,
            "external_ref": _EXT_REF,
            "notification_event_types": ["run_completed"],
        },
    )

    subs = await backend.list_subscriptions(
        enabled_only=True, principal_id=principal.id,
    )
    assert len(subs) == 1
    assert subs[0].filter.event_types == ["run_completed"]


async def test_create_consumer_principal_without_event_types_errors() -> None:
    """Consumer principals MUST specify notification_event_types."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    with pytest.raises(ValueError, match="must specify notification_event_types"):
        await dispatch(
            ctx,
            "create_principal",
            {"kind": "consumer", "external_ref": _EXT_REF},
        )

    # Nothing was written.
    assert await storage.list_principals() == []


async def test_create_app_kind_principal_without_event_types_errors() -> None:
    """App-registered kinds MUST specify notification_event_types."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    with pytest.raises(ValueError, match="must specify notification_event_types"):
        await dispatch(
            ctx,
            "create_principal",
            {"kind": _APP_KIND, "external_ref": _EXT_REF},
        )


async def test_create_run_kind_principal_rejects_event_types() -> None:
    """Run principals do not support self-subscriptions."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    with pytest.raises(
        ValueError, match="do not support self-subscriptions",
    ):
        await dispatch(
            ctx,
            "create_principal",
            {
                "kind": "run",
                "external_ref": _EXT_REF,
                "notification_event_types": ["run_completed"],
            },
        )


async def test_create_source_kind_principal_rejects_event_types() -> None:
    """Source principals do not support self-subscriptions."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    with pytest.raises(
        ValueError, match="do not support self-subscriptions",
    ):
        await dispatch(
            ctx,
            "create_principal",
            {
                "kind": "source",
                "external_ref": _EXT_REF,
                "notification_event_types": ["something"],
            },
        )


async def test_create_run_principal_with_none_event_types_succeeds() -> None:
    """Run principals with notification_event_types=None succeed (no subscription)."""
    storage = InMemoryPrincipalStorage()
    backend = InMemoryDispatchBackend()
    ctx = _context(storage, dispatch_backend=backend)

    principal = await dispatch(
        ctx,
        "create_principal",
        {
            "kind": "run",
            "external_ref": _EXT_REF,
            "notification_event_types": None,
        },
    )

    assert isinstance(principal, Principal)
    # No subscription created.
    subs = await backend.list_subscriptions(
        enabled_only=True, principal_id=principal.id,
    )
    assert len(subs) == 0
