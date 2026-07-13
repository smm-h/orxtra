"""Tests for the capability dispatcher."""

from __future__ import annotations

import importlib.util as _ilu
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
    Capability,
    TrustTier,
)
from orxtra.services._dispatcher import DispatchContext, dispatch
from pydantic import BaseModel, ValidationError

_spec = _ilu.spec_from_file_location(
    "tests.shared_mocks",
    Path(__file__).resolve().parents[2] / "tests" / "shared_mocks.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
make_auth_context = _mod.make_auth_context


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
    return DispatchContext(pool=mock_pool, auth_context=make_auth_context())


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
    ctx = DispatchContext(auth_context=make_auth_context())  # no pool

    result = await dispatch(ctx, "show_pricing", {})

    assert result == {"model": {}}
    mock_fn.assert_awaited_once_with()


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_validate_agent_no_pool(mock_get_fn: MagicMock) -> None:
    mock_fn = AsyncMock(return_value=[])
    mock_get_fn.return_value = mock_fn
    ctx = DispatchContext(auth_context=make_auth_context())  # no pool

    result = await dispatch(ctx, "validate_agent", {"path": "/agents/test.toml"})

    assert result == []
    mock_fn.assert_awaited_once_with(path=Path("/agents/test.toml"))


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_subscribe_requires_backend(mock_get_fn: MagicMock) -> None:
    ctx = DispatchContext(auth_context=make_auth_context())  # no backend
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
    ctx = DispatchContext(
        dispatch_backend=mock_backend, auth_context=make_auth_context()
    )

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
    ctx = DispatchContext(auth_context=make_auth_context())  # no pool
    with pytest.raises(ValueError, match="requires a database pool"):
        await dispatch(ctx, "list_runs", {})


@pytest.mark.asyncio
async def test_dispatch_invalid_params() -> None:
    ctx = DispatchContext(pool=AsyncMock(), auth_context=make_auth_context())
    with pytest.raises(ValidationError):
        await dispatch(ctx, "get_run", {"wrong_param": "value"})


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_start_run_path_coercion(
    mock_get_fn: MagicMock, mock_pool: AsyncMock
) -> None:
    mock_fn = AsyncMock(return_value=UUID("00000000-0000-0000-0000-000000000001"))
    mock_get_fn.return_value = mock_fn

    # start_run now injects pool, principal_storage, and the derived
    # caller_principal (resolved to the system principal for a SYSTEM-tier
    # operator context), then the coerced params.
    storage = InMemoryPrincipalStorage()
    system = await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
    )
    ctx = DispatchContext(
        pool=mock_pool,
        principal_storage=storage,
        auth_context=make_auth_context(),
    )

    await dispatch(
        ctx,
        "start_run",
        {"config_path": "/etc/run.toml", "intent": "test"},
    )

    call_args = mock_fn.call_args
    assert call_args[0][0] is mock_pool
    assert call_args[0][1] is storage
    assert call_args[0][2] == system
    call_kwargs = call_args[1]
    assert isinstance(call_kwargs["config_path"], Path)
    assert call_kwargs["intent"] == "test"


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_get_principal_routes_storage(mock_get_fn: MagicMock) -> None:
    """get_principal receives the principal_storage as its only positional arg."""
    mock_fn = AsyncMock(return_value=None)
    mock_get_fn.return_value = mock_fn
    mock_storage = AsyncMock()
    ctx = DispatchContext(
        principal_storage=mock_storage, auth_context=make_auth_context()
    )
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
        auth_context=make_auth_context(),
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
    ctx = DispatchContext(auth_context=make_auth_context())  # no principal_storage
    with pytest.raises(ValueError, match="requires a principal storage backend"):
        await dispatch(
            ctx,
            "get_principal",
            {"principal_id": "12345678-1234-1234-1234-123456789abc"},
        )


@pytest.mark.asyncio
async def test_dispatch_kind_registry_required_error() -> None:
    # Storage present, but the registry the capability also declares is absent.
    ctx = DispatchContext(
        principal_storage=AsyncMock(), auth_context=make_auth_context()
    )
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
        auth_context=make_auth_context(
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
    # A caller_principal-declaring capability with no auth context is stopped
    # by the dispatch-level authorization guard, which fires BEFORE any inject
    # resolution. The message is the dispatch guard's, not the inject helper's:
    # enforcement now supersedes the (still type-narrowing) inject-level check.
    mock_get_cap.return_value = _CALLER_PRINCIPAL_CAP
    mock_get_fn.return_value = AsyncMock()
    ctx = DispatchContext(principal_storage=InMemoryPrincipalStorage())

    with pytest.raises(
        ValueError, match="requires an authenticated context to dispatch"
    ):
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
        auth_context=make_auth_context(
            frozenset({SCOPE_RUNS_READ}),
            trust_tier=TrustTier.SYSTEM,
            consumer_id=None,
        ),
    )

    with pytest.raises(ValueError, match="requires a principal storage backend"):
        await dispatch(ctx, "_caller_principal_probe", {})


# -- Enforcement contracts (Phase 2.7: enforcement is LIVE) --
#
# dispatch() now enforces authorization at the choke point: an absent
# auth_context is a hard error, and a context missing the capability's
# required scope raises AuthorizationError.
# test_dispatch_scoped_capability_with_correct_scope_succeeds proves the
# correctly-scoped path still dispatches unchanged.


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_scoped_capability_without_auth_context_raises(
    mock_get_fn: MagicMock,
) -> None:
    mock_get_fn.return_value = AsyncMock(return_value=[])
    ctx = DispatchContext(pool=AsyncMock(), auth_context=None)

    with pytest.raises(ValueError, match="auth"):
        await dispatch(ctx, "list_runs", {})


@pytest.mark.asyncio
@patch("orxtra.services._dispatcher.get_capability_fn")
async def test_dispatch_scoped_capability_missing_scope_raises(
    mock_get_fn: MagicMock,
) -> None:
    mock_get_fn.return_value = AsyncMock(return_value=[])
    # list_runs requires SCOPE_RUNS_READ; this context lacks it.
    ctx = DispatchContext(
        pool=AsyncMock(),
        auth_context=make_auth_context(frozenset()),
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
        auth_context=make_auth_context(frozenset({SCOPE_RUNS_READ})),
    )

    result = await dispatch(ctx, "list_runs", {})

    assert result == []
    mock_fn.assert_awaited_once()
