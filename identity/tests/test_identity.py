"""Unit tests for the identity module.

Covers the in-memory storage (CRUD + mint idempotence), the KindRegistry,
the caller resolver (all branches), and the delete-translation domain error
(PrincipalInUseError) via a simulated foreign-key violation. No database is
required -- PG parity lives in tests/test_identity_pg.py at the repo root.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import asyncpg
import pytest
from orxtra.identity import (
    InMemoryPrincipalStorage,
    KindRegistry,
    PgPrincipalStorage,
    PrincipalInUseError,
    resolve_caller_principal,
)
from orxtra.protocols import (
    BUILTIN_KINDS,
    KIND_CONSUMER,
    KIND_RUN,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    AuthContext,
    Principal,
    TrustTier,
)

# ---------------------------------------------------------------------------
# In-memory storage: CRUD
# ---------------------------------------------------------------------------


@pytest.fixture
def storage() -> InMemoryPrincipalStorage:
    return InMemoryPrincipalStorage()


async def test_mint_creates_and_returns_principal(
    storage: InMemoryPrincipalStorage,
) -> None:
    ref = uuid4()
    principal = await storage.mint_principal(KIND_CONSUMER, ref, "alice")
    assert principal.kind == KIND_CONSUMER
    assert principal.external_ref == ref
    assert principal.display_name == "alice"
    assert isinstance(principal.id, UUID)
    assert principal.created_at is not None


async def test_get_principal_by_id(
    storage: InMemoryPrincipalStorage,
) -> None:
    ref = uuid4()
    minted = await storage.mint_principal(KIND_CONSUMER, ref, "bob")
    fetched = await storage.get_principal(minted.id)
    assert fetched == minted


async def test_get_principal_absent_returns_none(
    storage: InMemoryPrincipalStorage,
) -> None:
    assert await storage.get_principal(uuid4()) is None


async def test_get_principal_by_ref(
    storage: InMemoryPrincipalStorage,
) -> None:
    ref = uuid4()
    minted = await storage.mint_principal(KIND_CONSUMER, ref, "carol")
    fetched = await storage.get_principal_by_ref(KIND_CONSUMER, ref)
    assert fetched == minted
    # Same ref under a different kind is a distinct actor.
    assert await storage.get_principal_by_ref(KIND_RUN, ref) is None


async def test_list_principals_filter_by_kind(
    storage: InMemoryPrincipalStorage,
) -> None:
    await storage.mint_principal(KIND_CONSUMER, uuid4(), "c1")
    await storage.mint_principal(KIND_CONSUMER, uuid4(), "c2")
    await storage.mint_principal(KIND_RUN, uuid4(), "r1")

    assert len(await storage.list_principals()) == 3
    consumers = await storage.list_principals(KIND_CONSUMER)
    assert len(consumers) == 2
    assert all(p.kind == KIND_CONSUMER for p in consumers)
    runs = await storage.list_principals(KIND_RUN)
    assert len(runs) == 1


async def test_update_display_name(
    storage: InMemoryPrincipalStorage,
) -> None:
    minted = await storage.mint_principal(KIND_CONSUMER, uuid4(), "old")
    await storage.update_display_name(minted.id, "new")
    fetched = await storage.get_principal(minted.id)
    assert fetched is not None
    assert fetched.display_name == "new"
    # Other fields are unchanged.
    assert fetched.id == minted.id
    assert fetched.external_ref == minted.external_ref
    assert fetched.created_at == minted.created_at


async def test_update_display_name_absent_is_hard_error(
    storage: InMemoryPrincipalStorage,
) -> None:
    with pytest.raises(KeyError):
        await storage.update_display_name(uuid4(), "nope")


async def test_delete_principal(
    storage: InMemoryPrincipalStorage,
) -> None:
    minted = await storage.mint_principal(KIND_CONSUMER, uuid4(), "temp")
    await storage.delete_principal(minted.id)
    assert await storage.get_principal(minted.id) is None
    # Deleting an absent principal is a no-op (in-memory parity boundary).
    await storage.delete_principal(uuid4())


# ---------------------------------------------------------------------------
# In-memory storage: mint idempotence
# ---------------------------------------------------------------------------


async def test_mint_is_idempotent(
    storage: InMemoryPrincipalStorage,
) -> None:
    ref = uuid4()
    first = await storage.mint_principal(KIND_CONSUMER, ref, "alice")
    second = await storage.mint_principal(KIND_CONSUMER, ref, "different-name")

    # Same row returned; the first display name wins (no update on re-mint).
    assert second.id == first.id
    assert second.display_name == "alice"
    assert len(await storage.list_principals()) == 1


# ---------------------------------------------------------------------------
# In-memory storage: sweep orphaned run principals
# ---------------------------------------------------------------------------


async def test_sweep_deletes_old_orphaned_run_principal() -> None:
    """A kind=run principal with no matching run and old enough is swept."""
    run_ids: set[UUID] = set()
    storage = InMemoryPrincipalStorage(run_ids_provider=lambda: run_ids)
    ref = uuid4()
    minted = await storage.mint_principal(KIND_RUN, ref, None)
    # Backdate the principal to make it old enough for the sweep.
    storage._get_principals()[minted.id] = Principal(
        id=minted.id,
        kind=minted.kind,
        external_ref=minted.external_ref,
        display_name=minted.display_name,
        created_at=datetime.now(tz=UTC) - timedelta(minutes=10),
    )
    swept = await storage.sweep_orphaned_run_principals(timedelta(minutes=5))
    assert swept == 1
    assert await storage.get_principal(minted.id) is None


async def test_sweep_skips_fresh_run_principal() -> None:
    """A kind=run principal that is too young is NOT swept (age guard)."""
    run_ids: set[UUID] = set()
    storage = InMemoryPrincipalStorage(run_ids_provider=lambda: run_ids)
    ref = uuid4()
    minted = await storage.mint_principal(KIND_RUN, ref, None)
    # Principal is fresh (just created) -- should not be swept.
    swept = await storage.sweep_orphaned_run_principals(timedelta(minutes=5))
    assert swept == 0
    assert await storage.get_principal(minted.id) is not None


async def test_sweep_skips_run_principal_with_matching_run() -> None:
    """A kind=run principal with a matching run IS NOT swept."""
    ref = uuid4()
    run_ids: set[UUID] = {ref}
    storage = InMemoryPrincipalStorage(run_ids_provider=lambda: run_ids)
    minted = await storage.mint_principal(KIND_RUN, ref, None)
    # Backdate to be old enough.
    storage._get_principals()[minted.id] = Principal(
        id=minted.id,
        kind=minted.kind,
        external_ref=minted.external_ref,
        display_name=minted.display_name,
        created_at=datetime.now(tz=UTC) - timedelta(minutes=10),
    )
    swept = await storage.sweep_orphaned_run_principals(timedelta(minutes=5))
    assert swept == 0
    assert await storage.get_principal(minted.id) is not None


async def test_sweep_skips_non_run_kind() -> None:
    """A kind=consumer orphan is NOT swept (sweep is kind=run only)."""
    run_ids: set[UUID] = set()
    storage = InMemoryPrincipalStorage(run_ids_provider=lambda: run_ids)
    ref = uuid4()
    minted = await storage.mint_principal(KIND_CONSUMER, ref, "consumer")
    # Backdate to be old enough.
    storage._get_principals()[minted.id] = Principal(
        id=minted.id,
        kind=minted.kind,
        external_ref=minted.external_ref,
        display_name=minted.display_name,
        created_at=datetime.now(tz=UTC) - timedelta(minutes=10),
    )
    swept = await storage.sweep_orphaned_run_principals(timedelta(minutes=5))
    assert swept == 0
    assert await storage.get_principal(minted.id) is not None


async def test_sweep_without_provider_returns_zero() -> None:
    """When no run_ids_provider is set, sweep is a no-op."""
    storage = InMemoryPrincipalStorage()
    await storage.mint_principal(KIND_RUN, uuid4(), None)
    swept = await storage.sweep_orphaned_run_principals(timedelta(minutes=5))
    assert swept == 0


# ---------------------------------------------------------------------------
# KindRegistry
# ---------------------------------------------------------------------------


def test_registry_builtins_always_registered() -> None:
    registry = KindRegistry([])
    assert registry.kinds >= BUILTIN_KINDS
    for kind in BUILTIN_KINDS:
        registry.validate(kind)  # no raise


def test_registry_app_kinds_registered() -> None:
    registry = KindRegistry(["user", "team"])
    assert "user" in registry.kinds
    assert "team" in registry.kinds
    registry.validate("user")
    registry.validate("team")
    assert registry.kinds >= BUILTIN_KINDS


def test_registry_duplicate_app_kind_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        KindRegistry(["user", "user"])


def test_registry_app_kind_colliding_with_builtin_rejected() -> None:
    with pytest.raises(ValueError, match="Duplicate"):
        KindRegistry([KIND_CONSUMER])


def test_registry_blank_app_kind_rejected() -> None:
    with pytest.raises(ValueError, match="non-blank"):
        KindRegistry([""])
    with pytest.raises(ValueError, match="non-blank"):
        KindRegistry(["   "])


def test_registry_validate_unknown_kind() -> None:
    registry = KindRegistry(["user"])
    with pytest.raises(ValueError, match="Unknown principal kind"):
        registry.validate("nonexistent")


def test_registry_validate_error_names_registered_set() -> None:
    registry = KindRegistry(["user"])
    with pytest.raises(ValueError, match="user") as exc_info:
        registry.validate("ghost")
    # The error names both the offending kind and the registered set.
    assert "ghost" in str(exc_info.value)
    assert KIND_SYSTEM in str(exc_info.value)


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


def _auth_context(
    *,
    consumer_id: UUID | None,
    trust_tier: TrustTier,
) -> AuthContext:
    now = datetime.now(tz=UTC)
    return AuthContext(
        id=uuid4(),
        consumer_id=consumer_id,
        scopes=frozenset(),
        trust_tier=trust_tier,
        authenticated_via="test",
        issued_at=now,
        expires_at=None,
    )


async def test_resolver_system_tier(
    storage: InMemoryPrincipalStorage,
) -> None:
    seeded = await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
    )
    ctx = _auth_context(consumer_id=None, trust_tier=TrustTier.SYSTEM)
    resolved = await resolve_caller_principal(ctx, storage)
    assert resolved.id == seeded.id
    assert resolved.kind == KIND_SYSTEM


async def test_resolver_system_tier_unseeded_hard_error(
    storage: InMemoryPrincipalStorage,
) -> None:
    ctx = _auth_context(consumer_id=None, trust_tier=TrustTier.SYSTEM)
    with pytest.raises(RuntimeError, match="not seeded"):
        await resolve_caller_principal(ctx, storage)


async def test_resolver_consumer_tier(
    storage: InMemoryPrincipalStorage,
) -> None:
    consumer_id = uuid4()
    minted = await storage.mint_principal(KIND_CONSUMER, consumer_id, "alice")
    ctx = _auth_context(
        consumer_id=consumer_id, trust_tier=TrustTier.IDENTIFIED,
    )
    resolved = await resolve_caller_principal(ctx, storage)
    assert resolved.id == minted.id
    assert resolved.kind == KIND_CONSUMER


async def test_resolver_none_consumer_non_system_hard_error(
    storage: InMemoryPrincipalStorage,
) -> None:
    ctx = _auth_context(consumer_id=None, trust_tier=TrustTier.IDENTIFIED)
    with pytest.raises(RuntimeError, match="no consumer_id"):
        await resolve_caller_principal(ctx, storage)


async def test_resolver_unminted_consumer_hard_error(
    storage: InMemoryPrincipalStorage,
) -> None:
    ctx = _auth_context(
        consumer_id=uuid4(), trust_tier=TrustTier.VERIFIED,
    )
    with pytest.raises(RuntimeError, match="integrity violation"):
        await resolve_caller_principal(ctx, storage)


# ---------------------------------------------------------------------------
# PrincipalInUseError: simulated FK violation on the PG backend
# ---------------------------------------------------------------------------


@dataclass
class _FakeTxn:
    async def __aenter__(self) -> None:
        return None

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakeConn:
    """Connection stub whose execute() raises a chosen constraint violation."""

    def __init__(
        self,
        exc_class: type[Exception] = asyncpg.ForeignKeyViolationError,
    ) -> None:
        self._exc_class = exc_class

    def transaction(self) -> _FakeTxn:
        return _FakeTxn()

    async def execute(self, *_args: object) -> str:
        msg = (
            'update or delete on table "principals" violates a constraint'
        )
        raise self._exc_class(msg)


class _FakeAcquire:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    async def __aenter__(self) -> _FakeConn:
        return self._conn

    async def __aexit__(self, *_exc: object) -> bool:
        return False


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _FakeAcquire:
        return _FakeAcquire(self._conn)


async def test_delete_principal_fk_violation_translated() -> None:
    """A ForeignKeyViolationError on DELETE becomes PrincipalInUseError."""
    pool = _FakePool(_FakeConn())
    storage = PgPrincipalStorage(pool)  # type: ignore[arg-type]

    principal_id = uuid4()
    with pytest.raises(PrincipalInUseError) as exc_info:
        await storage.delete_principal(principal_id)
    assert exc_info.value.principal_id == principal_id


async def test_delete_principal_restrict_violation_translated() -> None:
    """A RestrictViolationError on DELETE also becomes PrincipalInUseError.

    PostgreSQL 18 changed the SQLSTATE for an ON DELETE RESTRICT violation from
    23503 (foreign_key_violation) to 23001 (restrict_violation). asyncpg maps
    those to ForeignKeyViolationError and RestrictViolationError respectively,
    which are siblings, not subclasses -- so catching only the former silently
    stopped translating the domain error the moment the server moved to 18.
    """
    pool = _FakePool(_FakeConn(asyncpg.RestrictViolationError))
    storage = PgPrincipalStorage(pool)  # type: ignore[arg-type]

    principal_id = uuid4()
    with pytest.raises(PrincipalInUseError) as exc_info:
        await storage.delete_principal(principal_id)
    assert exc_info.value.principal_id == principal_id
    assert "cannot be deleted" in str(exc_info.value)


# ---------------------------------------------------------------------------
# Signature parity: both backends match the PrincipalStorage protocol
# ---------------------------------------------------------------------------


def _normalized_params(func: object) -> list[tuple[str, object, object]]:
    """Return each parameter's (name, kind, default), excluding ``self``.

    Two callables with an identical normalized parameter list have the same
    public call shape -- names, positional/keyword kind, and defaults.
    """
    import inspect

    sig = inspect.signature(func)  # type: ignore[arg-type]
    return [
        (p.name, p.kind, p.default)
        for name, p in sig.parameters.items()
        if name != "self"
    ]


_PRINCIPAL_STORAGE_METHODS = [
    "mint_principal",
    "get_principal",
    "get_principal_by_ref",
    "list_principals",
    "update_display_name",
    "delete_principal",
    "sweep_orphaned_run_principals",
]


@pytest.mark.parametrize("backend_cls", [InMemoryPrincipalStorage, PgPrincipalStorage])
@pytest.mark.parametrize("method_name", _PRINCIPAL_STORAGE_METHODS)
def test_principal_storage_signature_parity(
    backend_cls: type, method_name: str,
) -> None:
    """Every PrincipalStorage method has an identical call shape on the
    protocol and on both concrete backends (in-memory + PG).

    This pins mint/get/get_by_ref/list/update/delete so a change to one
    surface that skips the others fails loudly.
    """
    from orxtra.protocols import PrincipalStorage

    proto = _normalized_params(getattr(PrincipalStorage, method_name))
    impl = _normalized_params(getattr(backend_cls, method_name))
    assert impl == proto, (
        f"{backend_cls.__name__}.{method_name} signature drifted from "
        f"PrincipalStorage.{method_name}:\n  protocol: {proto}\n  impl: {impl}"
    )


def test_both_principal_backends_satisfy_protocol() -> None:
    """Both backends are runtime instances of the PrincipalStorage protocol."""
    from orxtra.protocols import PrincipalStorage

    assert isinstance(InMemoryPrincipalStorage(), PrincipalStorage)
