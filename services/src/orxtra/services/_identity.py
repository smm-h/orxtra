"""Principal CRUD service functions.

Thin wrappers over ``PrincipalStorage`` that add the service-layer policy
the storage deliberately omits: kind validation (via ``KindRegistry``) and
the refusal to delete the singleton system principal. Storage accepts any
string kind and deletes any row; the enforcement point lives here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.protocols import KIND_SYSTEM

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.identity import KindRegistry
    from orxtra.protocols import Principal, PrincipalStorage


async def create_principal(
    principal_storage: PrincipalStorage,
    kind_registry: KindRegistry,
    *,
    kind: str,
    external_ref: UUID,
    display_name: str | None = None,
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
    """
    if kind == KIND_SYSTEM:
        msg = (
            "Refusing to create a system principal. The system principal is a "
            "seeded singleton and cannot be minted via the API -- it is "
            "created once during database seeding."
        )
        raise ValueError(msg)
    kind_registry.validate(kind)
    return await principal_storage.mint_principal(kind, external_ref, display_name)


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
