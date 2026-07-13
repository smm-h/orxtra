from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from orxtra.protocols import (
    BUILTIN_KINDS,
    KIND_CONSUMER,
    KIND_RUN,
    KIND_SOURCE,
    KIND_SYSTEM,
    SCOPE_PRINCIPALS_MANAGE,
    SCOPE_PRINCIPALS_READ,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    Principal,
    PrincipalStorage,
)

# -- Principal --


class TestPrincipal:
    def test_construction_all_fields(self) -> None:
        now = datetime.now(tz=UTC)
        pid = uuid4()
        ref = uuid4()
        p = Principal(
            id=pid,
            kind=KIND_RUN,
            external_ref=ref,
            display_name="run-42",
            created_at=now,
        )
        assert p.id == pid
        assert p.kind == KIND_RUN
        assert p.external_ref == ref
        assert p.display_name == "run-42"
        assert p.created_at == now

    def test_construction_none_display_name(self) -> None:
        p = Principal(
            id=uuid4(),
            kind=KIND_SYSTEM,
            external_ref=SYSTEM_PRINCIPAL_EXTERNAL_REF,
            display_name=None,
            created_at=datetime.now(tz=UTC),
        )
        assert p.display_name is None

    def test_frozen(self) -> None:
        p = Principal(
            id=uuid4(),
            kind=KIND_CONSUMER,
            external_ref=uuid4(),
            display_name="client",
            created_at=datetime.now(tz=UTC),
        )
        with pytest.raises(FrozenInstanceError):
            p.kind = KIND_SOURCE  # type: ignore[misc]


# -- Built-in kinds --


class TestBuiltinKinds:
    def test_kind_values(self) -> None:
        assert KIND_RUN == "run"
        assert KIND_CONSUMER == "consumer"
        assert KIND_SOURCE == "source"
        assert KIND_SYSTEM == "system"

    def test_builtin_kinds_membership_pinned(self) -> None:
        assert frozenset({
            "run",
            "consumer",
            "source",
            "system",
        }) == BUILTIN_KINDS
        assert len(BUILTIN_KINDS) == 4

    def test_builtin_kinds_contains_constants(self) -> None:
        assert KIND_RUN in BUILTIN_KINDS
        assert KIND_CONSUMER in BUILTIN_KINDS
        assert KIND_SOURCE in BUILTIN_KINDS
        assert KIND_SYSTEM in BUILTIN_KINDS


# -- System sentinel --


class TestSystemSentinel:
    def test_sentinel_is_all_zeros(self) -> None:
        assert UUID(int=0) == SYSTEM_PRINCIPAL_EXTERNAL_REF
        assert str(SYSTEM_PRINCIPAL_EXTERNAL_REF) == (
            "00000000-0000-0000-0000-000000000000"
        )


# -- Scope constants --


class TestPrincipalScopes:
    def test_scope_values_pinned(self) -> None:
        assert SCOPE_PRINCIPALS_READ == "principals:read"
        assert SCOPE_PRINCIPALS_MANAGE == "principals:manage"


# -- PrincipalStorage protocol --


class _StubPrincipalStorage:
    """Minimal structural PrincipalStorage implementation."""

    async def mint_principal(
        self,
        kind: str,
        external_ref: UUID,
        display_name: str | None,
    ) -> Principal:
        return Principal(
            id=uuid4(),
            kind=kind,
            external_ref=external_ref,
            display_name=display_name,
            created_at=datetime.now(tz=UTC),
        )

    async def get_principal(self, principal_id: UUID) -> Principal | None:
        return None

    async def get_principal_by_ref(
        self,
        kind: str,
        external_ref: UUID,
    ) -> Principal | None:
        return None

    async def list_principals(self, kind: str | None = None) -> list[Principal]:
        return []

    async def update_display_name(
        self,
        principal_id: UUID,
        display_name: str,
    ) -> None:
        return None

    async def delete_principal(self, principal_id: UUID) -> None:
        return None

    async def sweep_orphaned_run_principals(
        self, older_than: timedelta,
    ) -> int:
        return 0


class TestPrincipalStorage:
    def test_runtime_checkable(self) -> None:
        storage = _StubPrincipalStorage()
        assert isinstance(storage, PrincipalStorage)

    def test_non_conforming_rejected(self) -> None:
        assert not isinstance(object(), PrincipalStorage)
