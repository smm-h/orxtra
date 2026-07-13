"""End-to-end tests for the principal CRUD capabilities.

Each capability is exercised through the generic dispatcher with a real
``InMemoryPrincipalStorage`` and ``KindRegistry``, verifying the service-layer
policy: kind validation, idempotent minting, system-principal delete refusal,
and ``PrincipalInUseError`` propagation.
"""

from __future__ import annotations

from uuid import UUID

import pytest
from orxtra.identity import (
    InMemoryPrincipalStorage,
    KindRegistry,
    PrincipalInUseError,
)
from orxtra.protocols import KIND_SYSTEM, Principal
from orxtra.services import DispatchContext, dispatch

_APP_KIND = "user"
_EXT_REF = "11111111-1111-1111-1111-111111111111"


def _context(
    storage: InMemoryPrincipalStorage,
    *,
    app_kinds: tuple[str, ...] = (_APP_KIND,),
) -> DispatchContext:
    return DispatchContext(
        principal_storage=storage,
        kind_registry=KindRegistry(app_kinds),
    )


async def test_create_principal_validates_known_kind() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    principal = await dispatch(
        ctx,
        "create_principal",
        {"kind": _APP_KIND, "external_ref": _EXT_REF, "display_name": "Ann"},
    )

    assert isinstance(principal, Principal)
    assert principal.kind == _APP_KIND
    assert principal.external_ref == UUID(_EXT_REF)
    assert principal.display_name == "Ann"


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
        {"kind": _APP_KIND, "external_ref": _EXT_REF, "display_name": "Ann"},
    )
    second = await dispatch(
        ctx,
        "create_principal",
        {"kind": _APP_KIND, "external_ref": _EXT_REF, "display_name": "Ann"},
    )

    assert first.id == second.id
    assert len(await storage.list_principals()) == 1


async def test_get_principal_round_trip() -> None:
    storage = InMemoryPrincipalStorage()
    ctx = _context(storage)

    created = await dispatch(
        ctx,
        "create_principal",
        {"kind": _APP_KIND, "external_ref": _EXT_REF},
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
        {"kind": _APP_KIND, "external_ref": _EXT_REF},
    )
    await dispatch(
        ctx,
        "create_principal",
        {"kind": "consumer", "external_ref": "33333333-3333-3333-3333-333333333333"},
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
        {"kind": _APP_KIND, "external_ref": _EXT_REF},
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
        {"kind": _APP_KIND, "external_ref": _EXT_REF},
    )

    with pytest.raises(PrincipalInUseError):
        await dispatch(ctx, "delete_principal", {"principal_id": str(created.id)})
