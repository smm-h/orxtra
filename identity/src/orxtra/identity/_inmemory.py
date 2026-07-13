from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orxtra.protocols import Principal
from uuid6 import uuid7

if TYPE_CHECKING:
    from uuid import UUID


class InMemoryPrincipalStorage:
    """Dict-backed principal storage for tests.

    Pool-free: all methods operate on internal dicts directly, matching the
    API of ``PgPrincipalStorage`` with identical semantics, including
    idempotent minting on ``(kind, external_ref)``.

    Parity boundary: the PG backend translates a RESTRICT foreign-key
    violation on delete into ``PrincipalInUseError``. In-memory has no
    foreign keys, so ``delete_principal`` here always deletes -- there is
    nothing to enforce. This mirrors how the other in-memory backends skip
    FK enforcement (e.g. ``InMemoryAuthBackend`` cannot exercise the
    consumer/credential cascade). FK-translation behaviour is covered by the
    PG backend and its integration tests, not this stand-in.
    """

    def __init__(self) -> None:
        self._principals: dict[UUID, Principal] = {}

    async def mint_principal(
        self,
        kind: str,
        external_ref: UUID,
        display_name: str | None,
    ) -> Principal:
        existing = self._find_by_ref(kind, external_ref)
        if existing is not None:
            return existing
        principal = Principal(
            id=uuid7(),
            kind=kind,
            external_ref=external_ref,
            display_name=display_name,
            created_at=datetime.now(tz=UTC),
        )
        self._principals[principal.id] = principal
        return principal

    async def get_principal(self, principal_id: UUID) -> Principal | None:
        return self._principals.get(principal_id)

    async def get_principal_by_ref(
        self,
        kind: str,
        external_ref: UUID,
    ) -> Principal | None:
        return self._find_by_ref(kind, external_ref)

    async def list_principals(self, kind: str | None = None) -> list[Principal]:
        principals = sorted(
            self._principals.values(),
            key=lambda p: p.created_at,
        )
        if kind is None:
            return principals
        return [p for p in principals if p.kind == kind]

    async def update_display_name(
        self,
        principal_id: UUID,
        display_name: str,
    ) -> None:
        """Set the display name of an existing principal.

        Hard error (``KeyError``) if the principal is absent -- this is not
        an upsert, matching ``PgPrincipalStorage``.
        """
        existing = self._principals.get(principal_id)
        if existing is None:
            msg = f"Principal {principal_id} not found"
            raise KeyError(msg)
        self._principals[principal_id] = Principal(
            id=existing.id,
            kind=existing.kind,
            external_ref=existing.external_ref,
            display_name=display_name,
            created_at=existing.created_at,
        )

    async def delete_principal(self, principal_id: UUID) -> None:
        """Delete a principal.

        In-memory has no foreign keys, so this always deletes (see the class
        docstring's parity note). Absence is a no-op.
        """
        self._principals.pop(principal_id, None)

    def _find_by_ref(self, kind: str, external_ref: UUID) -> Principal | None:
        for principal in self._principals.values():
            if principal.kind == kind and principal.external_ref == external_ref:
                return principal
        return None

    # -- Expose internals for direct test manipulation --

    def _get_principals(self) -> dict[UUID, Principal]:
        return self._principals
