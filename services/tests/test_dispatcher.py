"""Tests for the capability dispatcher."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import pytest
from orxtra.auth import AuthorizationError
from orxtra.identity import InMemoryPrincipalStorage
from orxtra.protocols import (
    KIND_SYSTEM,
    SCOPE_RUNS_READ,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    AuthContext,
    Capability,
    TrustTier,
)
from orxtra.services._dispatcher import DispatchContext, dispatch
from pydantic import BaseModel, ValidationError

_DEFAULT_CONSUMER_ID = UUID(int=2)


def _auth_context(
    scopes: frozenset[str],
    *,
    trust_tier: TrustTier = TrustTier.IDENTIFIED,
    consumer_id: UUID | None = _DEFAULT_CONSUMER_ID,
) -> AuthContext:
    """Build an ephemeral AuthContext for dispatcher tests."""
    return AuthContext(
        id=UUID(int=1),
        consumer_id=consumer_id,
        scopes=scopes,
        trust_tier=trust_tier,
        authenticated_via="test",
        issued_at=datetime.now(UTC),
        expires_at=None,
    )


class _NoParams(BaseModel):
    """Empty params model for a synthetic caller_principal-declaring capability."""


# A synthetic capability that declares only the derived ``caller_principal``
# inject token. No real capability declares it yet, so -- following the way
# these tests simulate capabilities -- we patch get_capability to return this
# probe and exercise the resolution mechanics directly.
_CALLER_PRINCIPAL_CAP = Capability(
    name="_caller_principal_probe",
    namespace="test",
    description="probe",
    params_model=_NoParams,
    result_model=None,
    tags=frozenset(),
    category="test",
    required_scope=SCOPE_RUNS_READ,
    injects=frozenset({"caller_principal"}),
)


@pytest.fixture
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    conn = AsyncMock()
    ctx_manager = MagicMock()
    ctx_manager.__aenter__ = AsyncMock(return_value=conn)
    ctx_manager.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx_manager)
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def context(mock_pool: AsyncMock) -> DispatchContext:
    return DispatchContext(pool=mock_pool)


@pytest.mark.asyncio
async def test_dispatch_unknown_capability(context: DispatchContext) -> None:
    with pytest.raises(ValueError, match="Unknown capability"):
        await dispatch(context, "nonexistent", {})


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_list_runs(
    mock_get_fn: MagicMock, context: DispatchContext, mock_pool: AsyncMock
) -> None:
    mock_fn = AsyncMock(return_value=[])
    mock_get_fn.return_value = mock_fn

    result = await dispatch(context, "list_runs", {})

    assert result == []
    mock_fn.assert_awaited_once_with(mock_pool)


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_get_run(
    mock_get_fn: MagicMock, context: DispatchContext, mock_pool: AsyncMock
) -> None:
    mock_fn = AsyncMock(return_value=None)
    mock_get_fn.return_value = mock_fn
    run_id = "12345678-1234-1234-1234-123456789abc"

    result = await dispatch(context, "get_run", {"run_id": run_id})

    assert result is None
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_fire_event(
    mock_get_fn: MagicMock, context: DispatchContext, mock_pool: AsyncMock
) -> None:
    mock_fn = AsyncMock(return_value=UUID("00000000-0000-0000-0000-000000000001"))
    mock_get_fn.return_value = mock_fn
    run_id = "12345678-1234-1234-1234-123456789abc"

    result = await dispatch(
        context,
        "fire_event",
        {"run_id": run_id, "event_name": "deploy", "payload": {"key": "val"}},
    )

    assert result is not None
    mock_fn.assert_awaited_once_with(
        mock_pool,
        run_id=UUID(run_id),
        event_name="deploy",
        payload={"key": "val"},
    )


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_show_pricing_no_pool(mock_get_fn: MagicMock) -> None:
    mock_fn = AsyncMock(return_value={"model": {}})
    mock_get_fn.return_value = mock_fn
    ctx = DispatchContext()  # no pool

    result = await dispatch(ctx, "show_pricing", {})

    assert result == {"model": {}}
    mock_fn.assert_awaited_once_with()


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_validate_agent_no_pool(mock_get_fn: MagicMock) -> None:
    mock_fn = AsyncMock(return_value=[])
    mock_get_fn.return_value = mock_fn
    ctx = DispatchContext()  # no pool

    result = await dispatch(ctx, "validate_agent", {"path": "/agents/test.toml"})

    assert result == []
    mock_fn.assert_awaited_once_with(path=Path("/agents/test.toml"))


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_subscribe_requires_backend(mock_get_fn: MagicMock) -> None:
    ctx = DispatchContext()  # no backend
    with pytest.raises(ValueError, match="requires a dispatch backend"):
        await dispatch(
            ctx,
            "subscribe",
            {
                "filter": {"event_types": ["test"]},
                "actions": [{"message": "hi"}],
            },
        )


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_subscribe_with_backend(mock_get_fn: MagicMock) -> None:
    mock_fn = AsyncMock(return_value=UUID("00000000-0000-0000-0000-000000000001"))
    mock_get_fn.return_value = mock_fn
    mock_backend = AsyncMock()
    ctx = DispatchContext(dispatch_backend=mock_backend)

    await dispatch(
        ctx,
        "subscribe",
        {
            "filter": {"event_types": ["test"]},
            "actions": [{"message": "hi"}],
            "storage": "persistent",
        },
    )

    mock_fn.assert_awaited_once()
    # Backend should be the first positional arg
    call_args = mock_fn.call_args
    assert call_args[0][0] is mock_backend


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_pure_capability_ignores_pool(
    mock_get_fn: MagicMock, context: DispatchContext
) -> None:
    """A capability declaring no injects receives neither pool nor backend,
    even when the context provides them -- routing is declaration-driven."""
    mock_fn = AsyncMock(return_value={"model": {}})
    mock_get_fn.return_value = mock_fn

    result = await dispatch(context, "show_pricing", {})

    assert result == {"model": {}}
    mock_fn.assert_awaited_once_with()


@pytest.mark.asyncio
async def test_dispatch_pool_required_error() -> None:
    ctx = DispatchContext()  # no pool
    with pytest.raises(ValueError, match="requires a database pool"):
        await dispatch(ctx, "list_runs", {})


@pytest.mark.asyncio
async def test_dispatch_invalid_params() -> None:
    ctx = DispatchContext(pool=AsyncMock())
    with pytest.raises(ValidationError):
        await dispatch(ctx, "get_run", {"wrong_param": "value"})


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_start_run_path_coercion(
    mock_get_fn: MagicMock, context: DispatchContext, mock_pool: AsyncMock
) -> None:
    mock_fn = AsyncMock(return_value=UUID("00000000-0000-0000-0000-000000000001"))
    mock_get_fn.return_value = mock_fn

    await dispatch(
        context,
        "start_run",
        {"config_path": "/etc/run.toml", "intent": "test"},
    )

    call_kwargs = mock_fn.call_args[1]
    assert isinstance(call_kwargs["config_path"], Path)
    assert call_kwargs["intent"] == "test"


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_get_principal_routes_storage(mock_get_fn: MagicMock) -> None:
    """get_principal receives the principal_storage as its only positional arg."""
    mock_fn = AsyncMock(return_value=None)
    mock_get_fn.return_value = mock_fn
    mock_storage = AsyncMock()
    ctx = DispatchContext(principal_storage=mock_storage)
    principal_id = "12345678-1234-1234-1234-123456789abc"

    await dispatch(ctx, "get_principal", {"principal_id": principal_id})

    mock_fn.assert_awaited_once_with(mock_storage, principal_id=UUID(principal_id))


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_create_principal_routes_storage_and_registry(
    mock_get_fn: MagicMock,
) -> None:
    """create_principal receives principal_storage then kind_registry, in order."""
    mock_fn = AsyncMock(return_value=None)
    mock_get_fn.return_value = mock_fn
    mock_storage = AsyncMock()
    mock_registry = MagicMock()
    ctx = DispatchContext(
        principal_storage=mock_storage,
        kind_registry=mock_registry,
    )
    external_ref = "12345678-1234-1234-1234-123456789abc"

    await dispatch(
        ctx,
        "create_principal",
        {"kind": "user", "external_ref": external_ref, "display_name": "Ann"},
    )

    mock_fn.assert_awaited_once_with(
        mock_storage,
        mock_registry,
        kind="user",
        external_ref=UUID(external_ref),
        display_name="Ann",
    )
    # Storage is the first positional arg, registry the second.
    call_args = mock_fn.call_args
    assert call_args[0][0] is mock_storage
    assert call_args[0][1] is mock_registry


@pytest.mark.asyncio
async def test_dispatch_principal_storage_required_error() -> None:
    ctx = DispatchContext()  # no principal_storage
    with pytest.raises(ValueError, match="requires a principal storage backend"):
        await dispatch(
            ctx,
            "get_principal",
            {"principal_id": "12345678-1234-1234-1234-123456789abc"},
        )


@pytest.mark.asyncio
async def test_dispatch_kind_registry_required_error() -> None:
    # Storage present, but the registry the capability also declares is absent.
    ctx = DispatchContext(principal_storage=AsyncMock())
    with pytest.raises(ValueError, match="requires a principal kind registry"):
        await dispatch(
            ctx,
            "create_principal",
            {
                "kind": "user",
                "external_ref": "12345678-1234-1234-1234-123456789abc",
            },
        )


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_query_events_optional_params(
    mock_get_fn: MagicMock, context: DispatchContext, mock_pool: AsyncMock
) -> None:
    mock_fn = AsyncMock(return_value=[])
    mock_get_fn.return_value = mock_fn
    run_id = "12345678-1234-1234-1234-123456789abc"

    await dispatch(context, "query_events", {"run_id": run_id})

    call_kwargs = mock_fn.call_args[1]
    assert call_kwargs["event_type"] is None
    assert call_kwargs["since"] is None
    assert call_kwargs["limit"] == 100


# -- caller_principal inject token resolution --


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability")
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_resolves_caller_principal(
    mock_get_fn: MagicMock, mock_get_cap: MagicMock
) -> None:
    """A capability declaring ``caller_principal`` receives the resolved,
    persisted Principal as its final positional argument."""
    mock_get_cap.return_value = _CALLER_PRINCIPAL_CAP
    mock_fn = AsyncMock(return_value="ok")
    mock_get_fn.return_value = mock_fn

    storage = InMemoryPrincipalStorage()
    system = await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system"
    )
    ctx = DispatchContext(
        principal_storage=storage,
        auth_context=_auth_context(
            frozenset({SCOPE_RUNS_READ}),
            trust_tier=TrustTier.SYSTEM,
            consumer_id=None,
        ),
    )

    result = await dispatch(ctx, "_caller_principal_probe", {})

    assert result == "ok"
    # The resolved persisted Principal is passed positionally (and, being the
    # only declared inject, is the sole positional argument).
    mock_fn.assert_awaited_once_with(system)


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability")
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_caller_principal_requires_auth_context(
    mock_get_fn: MagicMock, mock_get_cap: MagicMock
) -> None:
    mock_get_cap.return_value = _CALLER_PRINCIPAL_CAP
    mock_get_fn.return_value = AsyncMock()
    ctx = DispatchContext(principal_storage=InMemoryPrincipalStorage())

    with pytest.raises(ValueError, match="requires an authenticated caller context"):
        await dispatch(ctx, "_caller_principal_probe", {})


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability")
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_caller_principal_requires_principal_storage(
    mock_get_fn: MagicMock, mock_get_cap: MagicMock
) -> None:
    mock_get_cap.return_value = _CALLER_PRINCIPAL_CAP
    mock_get_fn.return_value = AsyncMock()
    ctx = DispatchContext(
        auth_context=_auth_context(
            frozenset({SCOPE_RUNS_READ}),
            trust_tier=TrustTier.SYSTEM,
            consumer_id=None,
        ),
    )

    with pytest.raises(ValueError, match="requires a principal storage backend"):
        await dispatch(ctx, "_caller_principal_probe", {})


# -- Enforcement contracts (Phase 2.1a groundwork; NO enforcement yet) --
#
# The two xfail tests below describe the POST-enforcement world. The later
# subphase that wires the Authorizer into dispatch() flips them from xfail to
# passing. They are non-strict xfail so that, once enforcement lands and they
# start passing (xpass), the suite stays green without a forced failure.
# test_dispatch_scoped_capability_with_correct_scope_succeeds already passes
# today (no enforcement = everything passes) and must survive the flip
# unchanged.


@pytest.mark.xfail(
    reason="Enforcement wired in a later 2.1 subphase; no scope check yet.",
    strict=False,
)
@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_scoped_capability_without_auth_context_raises(
    mock_get_fn: MagicMock,
) -> None:
    mock_get_fn.return_value = AsyncMock(return_value=[])
    ctx = DispatchContext(pool=AsyncMock(), auth_context=None)

    with pytest.raises(ValueError, match="auth"):
        await dispatch(ctx, "list_runs", {})


@pytest.mark.xfail(
    reason="Enforcement wired in a later 2.1 subphase; no scope check yet.",
    strict=False,
)
@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_scoped_capability_missing_scope_raises(
    mock_get_fn: MagicMock,
) -> None:
    mock_get_fn.return_value = AsyncMock(return_value=[])
    # list_runs requires SCOPE_RUNS_READ; this context lacks it.
    ctx = DispatchContext(
        pool=AsyncMock(),
        auth_context=_auth_context(frozenset()),
    )

    with pytest.raises(AuthorizationError):
        await dispatch(ctx, "list_runs", {})


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_scoped_capability_with_correct_scope_succeeds(
    mock_get_fn: MagicMock,
) -> None:
    """A correctly-scoped context dispatches successfully. Passes today (no
    enforcement) and must keep passing once enforcement lands."""
    mock_fn = AsyncMock(return_value=[])
    mock_get_fn.return_value = mock_fn
    ctx = DispatchContext(
        pool=AsyncMock(),
        auth_context=_auth_context(frozenset({SCOPE_RUNS_READ})),
    )

    result = await dispatch(ctx, "list_runs", {})

    assert result == []
    mock_fn.assert_awaited_once()
