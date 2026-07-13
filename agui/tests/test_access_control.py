"""Access-control tests for the AG-UI SSE run stream.

The SSE endpoint authenticates the caller (via the compositor auth wall) and
must then verify the caller is allowed to stream the requested run:

- no ``auth_context`` in scope state -> 401 (open mode cannot stream runs);
- SYSTEM-tier operator -> may stream any run;
- a consumer -> may stream only runs whose ``created_by`` is their principal;
- run not found -> 404;
- consumer streaming another principal's run -> 403.

The ownership decision lives in ``_check_run_access`` (pure, no streaming);
the 401 and end-to-end wiring are exercised through the router with
``AsyncTestClient``.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from fastware import create_app
from fastware.testing import AsyncTestClient
from orxtra.agui._server import _check_run_access, create_agui_router
from orxtra.identity import InMemoryPrincipalStorage
from orxtra.protocols import KIND_CONSUMER, AuthContext, TrustTier

if TYPE_CHECKING:
    from orxtra.protocols import Principal

_GET_RUN = "orxtra.agui._server.get_run"


@dataclass(frozen=True)
class _FakeRun:
    """Minimal stand-in for RunReport; the check reads only ``created_by``."""

    created_by: UUID


def _consumer_ctx(consumer_id: UUID) -> AuthContext:
    return AuthContext(
        id=uuid4(),
        consumer_id=consumer_id,
        scopes=frozenset(),
        trust_tier=TrustTier.IDENTIFIED,
        authenticated_via="bearer",
        issued_at=datetime.now(tz=UTC),
        expires_at=None,
    )


def _system_ctx() -> AuthContext:
    return AuthContext(
        id=uuid4(),
        consumer_id=None,
        scopes=frozenset(),
        trust_tier=TrustTier.SYSTEM,
        authenticated_via="system",
        issued_at=datetime.now(tz=UTC),
        expires_at=None,
    )


async def _mint_consumer(
    storage: InMemoryPrincipalStorage,
) -> tuple[AuthContext, Principal]:
    consumer_id = uuid4()
    principal = await storage.mint_principal(KIND_CONSUMER, consumer_id, "consumer")
    return _consumer_ctx(consumer_id), principal


# ── Ownership decision (pure) ──


class TestCheckRunAccess:
    async def test_creator_streams_own_run(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, principal = await _mint_consumer(storage)
        run = _FakeRun(created_by=principal.id)
        with patch(_GET_RUN, new=AsyncMock(return_value=run)):
            result = await _check_run_access(
                ctx, str(uuid4()), pool=object(), principal_storage=storage,
            )
        assert result is None

    async def test_different_consumer_forbidden(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, _principal = await _mint_consumer(storage)
        run = _FakeRun(created_by=uuid4())  # owned by someone else
        with patch(_GET_RUN, new=AsyncMock(return_value=run)):
            result = await _check_run_access(
                ctx, str(uuid4()), pool=object(), principal_storage=storage,
            )
        assert result is not None
        assert result.status == 403

    async def test_system_operator_streams_anything(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx = _system_ctx()
        run = _FakeRun(created_by=uuid4())
        with patch(_GET_RUN, new=AsyncMock(return_value=run)) as mock_get_run:
            result = await _check_run_access(
                ctx, str(uuid4()), pool=object(), principal_storage=storage,
            )
        assert result is None
        # SYSTEM short-circuits: no run lookup is even needed.
        mock_get_run.assert_not_called()

    async def test_unknown_run_not_found(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, _principal = await _mint_consumer(storage)
        with patch(_GET_RUN, new=AsyncMock(return_value=None)):
            result = await _check_run_access(
                ctx, str(uuid4()), pool=object(), principal_storage=storage,
            )
        assert result is not None
        assert result.status == 404

    async def test_malformed_run_id_not_found(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, _principal = await _mint_consumer(storage)
        with patch(_GET_RUN, new=AsyncMock(return_value=None)) as mock_get_run:
            result = await _check_run_access(
                ctx, "not-a-uuid", pool=object(), principal_storage=storage,
            )
        assert result is not None
        assert result.status == 404
        mock_get_run.assert_not_called()

    async def test_missing_storage_is_hard_error(self) -> None:
        ctx = _consumer_ctx(uuid4())
        with pytest.raises(RuntimeError, match="misconfigured"):
            await _check_run_access(
                ctx, str(uuid4()), pool=object(), principal_storage=None,
            )


# ── End-to-end through the router ──


def _inject_auth_context(app: Any, auth_context: AuthContext | None) -> Any:
    """Wrap an ASGI app, seeding ``scope['state']['auth_context']``.

    Stands in for the compositor's auth wall so the handler sees an
    authenticated (or, when ``None``, unauthenticated) caller.
    """

    async def middleware(scope: dict[str, Any], receive: Any, send: Any) -> None:
        if scope["type"] == "http" and auth_context is not None:
            scope.setdefault("state", {})["auth_context"] = auth_context
        await app(scope, receive, send)

    return middleware


def _build_app(
    *,
    principal_storage: InMemoryPrincipalStorage,
    auth_context: AuthContext | None,
) -> Any:
    router, _broadcaster = create_agui_router(
        pool=object(),
        principal_storage=principal_storage,
    )
    return _inject_auth_context(create_app(router), auth_context)


class TestRouterIntegration:
    async def test_unauthenticated_gets_401(self) -> None:
        app = _build_app(
            principal_storage=InMemoryPrincipalStorage(),
            auth_context=None,
        )
        async with AsyncTestClient(app) as client:
            resp = await client.get("/events", params={"run_id": str(uuid4())})
            assert resp.status_code == 401

    async def test_missing_run_id_gets_400(self) -> None:
        app = _build_app(
            principal_storage=InMemoryPrincipalStorage(),
            auth_context=_consumer_ctx(uuid4()),
        )
        async with AsyncTestClient(app) as client:
            # Authenticated but no run_id.
            resp = await client.get("/events")
            assert resp.status_code == 400

    async def test_non_owner_gets_403(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, _principal = await _mint_consumer(storage)
        app = _build_app(principal_storage=storage, auth_context=ctx)
        run = _FakeRun(created_by=uuid4())
        with patch(_GET_RUN, new=AsyncMock(return_value=run)):
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    "/events", params={"run_id": str(uuid4())},
                )
                assert resp.status_code == 403

    async def test_unknown_run_gets_404(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, _principal = await _mint_consumer(storage)
        app = _build_app(principal_storage=storage, auth_context=ctx)
        with patch(_GET_RUN, new=AsyncMock(return_value=None)):
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    "/events", params={"run_id": str(uuid4())},
                )
                assert resp.status_code == 404

    async def test_creator_stream_proceeds(self) -> None:
        storage = InMemoryPrincipalStorage()
        ctx, principal = await _mint_consumer(storage)
        app = _build_app(principal_storage=storage, auth_context=ctx)
        run = _FakeRun(created_by=principal.id)
        with patch(_GET_RUN, new=AsyncMock(return_value=run)):
            async with AsyncTestClient(app) as client:
                async with asyncio.timeout(5), client.stream(
                    "GET", "/events", params={"run_id": str(uuid4())},
                ) as resp:
                    assert resp.status_code == 200
                    assert resp.headers["content-type"].startswith(
                        "text/event-stream",
                    )

    async def test_system_operator_stream_proceeds(self) -> None:
        app = _build_app(
            principal_storage=InMemoryPrincipalStorage(),
            auth_context=_system_ctx(),
        )
        async with AsyncTestClient(app) as client:  # noqa: SIM117 (stream needs client)
            async with asyncio.timeout(5), client.stream(
                "GET", "/events", params={"run_id": str(uuid4())},
            ) as resp:
                assert resp.status_code == 200
                assert resp.headers["content-type"].startswith(
                    "text/event-stream",
                )
