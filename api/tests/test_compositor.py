"""Tests for the HTTP compositor."""

from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from typing import Any, Self
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import httpx
import pytest
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
)
from fastware.testing import AsyncTestClient
from orxtra.a2a._skills import SkillRegistry
from orxtra.api._compositor import CompositorConfig, _build_mcp_app, create_compositor
from orxtra.auth import (
    Authenticator,
    HashCredentialVerifier,
    HmacCredentialVerifier,
    InMemoryAuthBackend,
)
from orxtra.identity import InMemoryPrincipalStorage
from orxtra.protocols import KIND_CONSUMER, TrustTier
from orxtra.services import DispatchContext

# -- Fixtures --


@pytest.fixture
def dispatch_context() -> DispatchContext:
    """DispatchContext with no pool (sufficient for routing tests)."""
    return DispatchContext()


@pytest.fixture
def agent_card() -> AgentCard:
    return AgentCard(
        name="test-agent",
        description="Test agent",
        version="0.0.1",
        supported_interfaces=[
            AgentInterface(url="http://localhost:8080/a2a"),
        ],
        capabilities=AgentCapabilities(streaming=True),
    )


@pytest.fixture
def skill_registry() -> SkillRegistry:
    return SkillRegistry([])


@pytest.fixture
def compositor_config(
    dispatch_context: DispatchContext,
    agent_card: AgentCard,
    skill_registry: SkillRegistry,
) -> CompositorConfig:
    return CompositorConfig(
        dispatch_context=dispatch_context,
        agent_card=agent_card,
        skill_registry=skill_registry,
    )


@pytest.fixture
def app(compositor_config: CompositorConfig) -> Any:
    return create_compositor(compositor_config)


@pytest.fixture
def auth_backend() -> InMemoryAuthBackend:
    return InMemoryAuthBackend()


@pytest.fixture
def authenticator(auth_backend: InMemoryAuthBackend) -> Authenticator:
    verifiers: dict[str, HashCredentialVerifier | HmacCredentialVerifier] = {
        "api_key": HashCredentialVerifier("api_key", auth_backend),
        "bearer": HashCredentialVerifier("bearer", auth_backend),
    }
    return Authenticator(auth_backend, verifiers)


@pytest.fixture
def authed_app(
    dispatch_context: DispatchContext,
    agent_card: AgentCard,
    skill_registry: SkillRegistry,
    authenticator: Authenticator,
) -> Any:
    """A compositor app with the auth wall enabled on every sub-app."""
    config = CompositorConfig(
        dispatch_context=dispatch_context,
        agent_card=agent_card,
        skill_registry=skill_registry,
        authenticator=authenticator,
    )
    return create_compositor(config)


async def _issue_token(backend: InMemoryAuthBackend) -> str:
    """Register a consumer with a bearer credential and return the raw token."""
    token = "valid-compositor-token"
    # In-memory has no principals FK, so stand-in consumer/principal ids suffice.
    consumer_id = await backend.create_consumer(
        "compositor-test-user",
        TrustTier.VERIFIED,
        ["api"],
        consumer_id=uuid4(),
        principal_id=uuid4(),
    )
    await backend.create_credential(consumer_id, "bearer", token)
    return token


# -- Tests --


class TestHealthEndpoint:
    async def test_returns_200(self, app: Any) -> None:
        async with AsyncTestClient(app) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    async def test_returns_ok_status(self, app: Any) -> None:
        async with AsyncTestClient(app) as client:
            resp = await client.get("/health")
            data = resp.json()
            assert data["status"] == "ok"


class TestAgentCardEndpoint:
    async def test_returns_200(self, app: Any) -> None:
        async with AsyncTestClient(app) as client:
            resp = await client.get("/.well-known/agent.json")
            assert resp.status_code == 200

    async def test_returns_valid_json(self, app: Any) -> None:
        async with AsyncTestClient(app) as client:
            resp = await client.get("/.well-known/agent.json")
            data = resp.json()
            assert data["name"] == "test-agent"
            assert data["version"] == "0.0.1"

    async def test_contains_capabilities(self, app: Any) -> None:
        async with AsyncTestClient(app) as client:
            resp = await client.get("/.well-known/agent.json")
            data = resp.json()
            assert "capabilities" in data
            assert data["capabilities"]["streaming"] is True


class TestMcpMount:
    async def test_mcp_path_is_mounted(self, app: Any) -> None:
        """Verify the /mcp path is handled by the MCP sub-app.

        The MCP StreamableHTTPSessionManager requires its lifespan to
        run before handling requests. Without lifespan, it raises
        RuntimeError with a message about the task group. We catch
        that as proof the mount worked -- the request reached the MCP
        session manager. A root-level 404 would mean the mount is
        missing entirely.
        """
        async with AsyncTestClient(app) as client:
            # The MCP app raises RuntimeError because the session manager
            # hasn't been initialized via lifespan. Either a 500 response
            # or the RuntimeError proves the mount worked.
            try:
                resp = await client.get("/mcp")
                # If we get a response, it should not be 404.
                assert resp.status_code != 404
            except RuntimeError as exc:
                # The RuntimeError from MCP's session manager proves
                # the request reached the MCP app.
                assert "Task group is not initialized" in str(exc)  # noqa: PT017


class TestA2aMount:
    async def test_a2a_agent_card_accessible(self, app: Any) -> None:
        """Verify the A2A agent card route at /a2a/.well-known/agent-card.json."""
        async with AsyncTestClient(app) as client:
            # The A2A SDK uses agent-card.json (with hyphen), not agent.json
            resp = await client.get("/a2a/.well-known/agent-card.json")
            assert resp.status_code == 200

    async def test_a2a_jsonrpc_endpoint(self, app: Any) -> None:
        """Verify the A2A JSON-RPC endpoint accepts POST at /a2a."""
        async with AsyncTestClient(app) as client:
            resp = await client.post(
                "/a2a",
                content=b'{"jsonrpc":"2.0","method":"message/send","id":1}',
                headers={"content-type": "application/json"},
            )
            # The A2A handler should respond (not 404).
            assert resp.status_code != 404


class TestAguiRoutes:
    async def test_agui_route_is_reachable(
        self, compositor_config: CompositorConfig,
    ) -> None:
        """Without auth, the AG-UI route is reachable (not 404)."""
        app = create_compositor(compositor_config)
        async with AsyncTestClient(app) as client:
            resp = await client.get("/ag-ui/events")
            # The route is found (not 404). The handler may return 400
            # (missing run_id) or 500 (handler bug) -- either proves routing.
            assert resp.status_code != 404

    async def test_agui_events_with_auth_rejects_unauthenticated(
        self,
        dispatch_context: DispatchContext,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
    ) -> None:
        """With an authenticator, unauthenticated requests get 401."""
        backend = InMemoryAuthBackend()
        verifiers: dict[str, HashCredentialVerifier | HmacCredentialVerifier] = {
            "api_key": HashCredentialVerifier("api_key", backend),
            "bearer": HashCredentialVerifier("bearer", backend),
        }
        authenticator = Authenticator(backend, verifiers)
        config = CompositorConfig(
            dispatch_context=dispatch_context,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        app = create_compositor(config)
        async with AsyncTestClient(app) as client:
            resp = await client.get("/ag-ui/events")
            assert resp.status_code == 401

    async def test_agui_events_with_auth_accepts_authenticated(
        self,
        authed_app: Any,
        auth_backend: InMemoryAuthBackend,
    ) -> None:
        """With a valid credential, the AG-UI route is reached (not 401)."""
        token = await _issue_token(auth_backend)
        async with AsyncTestClient(authed_app) as client:
            resp = await client.get(
                "/ag-ui/events",
                headers={"authorization": f"Bearer {token}"},
            )
            # Past the auth wall: the handler may 400 (missing run_id) or
            # 500, but never 401/404.
            assert resp.status_code not in (401, 404)


class TestAuthWall:
    """The auth wall wraps the MCP and A2A sub-apps, mirroring AG-UI."""

    async def test_mcp_rejects_unauthenticated(self, authed_app: Any) -> None:
        """Anonymous HTTP request to /mcp is blocked with 401."""
        async with AsyncTestClient(authed_app) as client:
            resp = await client.get("/mcp")
            assert resp.status_code == 401

    async def test_a2a_rejects_unauthenticated(self, authed_app: Any) -> None:
        """Anonymous HTTP request to /a2a is blocked with 401."""
        async with AsyncTestClient(authed_app) as client:
            resp = await client.post(
                "/a2a",
                content=b'{"jsonrpc":"2.0","method":"message/send","id":1}',
                headers={"content-type": "application/json"},
            )
            assert resp.status_code == 401

    async def test_a2a_agent_card_rejects_unauthenticated(
        self, authed_app: Any,
    ) -> None:
        """Even the A2A agent card route is behind the wall when authed."""
        async with AsyncTestClient(authed_app) as client:
            resp = await client.get("/a2a/.well-known/agent-card.json")
            assert resp.status_code == 401

    async def test_mcp_passes_authenticated_through(
        self, authed_app: Any, auth_backend: InMemoryAuthBackend,
    ) -> None:
        """A valid credential passes the wall and reaches the MCP sub-app.

        Past the wall, the MCP session manager raises RuntimeError because
        its lifespan was not run by the test client (see TestMcpMount).
        Either the RuntimeError or a non-401 response proves the request
        cleared the auth wall and reached the sub-app.
        """
        token = await _issue_token(auth_backend)
        async with AsyncTestClient(authed_app) as client:
            try:
                resp = await client.get(
                    "/mcp", headers={"authorization": f"Bearer {token}"},
                )
                assert resp.status_code != 401
            except RuntimeError as exc:
                assert "Task group is not initialized" in str(exc)  # noqa: PT017

    async def test_a2a_passes_authenticated_through(
        self, authed_app: Any, auth_backend: InMemoryAuthBackend,
    ) -> None:
        """A valid credential passes the wall and reaches the A2A sub-app."""
        token = await _issue_token(auth_backend)
        async with AsyncTestClient(authed_app) as client:
            resp = await client.get(
                "/a2a/.well-known/agent-card.json",
                headers={"authorization": f"Bearer {token}"},
            )
            assert resp.status_code not in (401, 404)

    async def test_root_agent_card_public_when_authenticated(
        self, authed_app: Any,
    ) -> None:
        """The ROOT /.well-known/agent.json stays public even with auth.

        It is the deliberate discovery surface, registered directly on the
        root router rather than behind the auth wall. Anonymous clients must
        still fetch it (200) so they can learn how to authenticate. The SDK's
        in-wall card at /a2a/.well-known/agent-card.json correctly stays 401
        (see test_a2a_agent_card_rejects_unauthenticated).
        """
        async with AsyncTestClient(authed_app) as client:
            resp = await client.get("/.well-known/agent.json")
            assert resp.status_code == 200
            assert resp.json()["name"] == "test-agent"

    async def test_mcp_unauthenticated_when_no_authenticator(
        self, app: Any,
    ) -> None:
        """With no authenticator, /mcp mounts raw (explicit open mode)."""
        async with AsyncTestClient(app) as client:
            try:
                resp = await client.get("/mcp")
                assert resp.status_code != 401
            except RuntimeError as exc:
                assert "Task group is not initialized" in str(exc)  # noqa: PT017

    async def test_a2a_unauthenticated_when_no_authenticator(
        self, app: Any,
    ) -> None:
        """With no authenticator, /a2a mounts raw (explicit open mode)."""
        async with AsyncTestClient(app) as client:
            resp = await client.get("/a2a/.well-known/agent-card.json")
            assert resp.status_code == 200


class TestWorkersWebSocket:
    """The native /workers/connect WebSocket is unaffected by the auth wall.

    The auth middleware wraps only the mounted sub-apps and passes non-HTTP
    scopes through untouched. The /workers/connect handler is registered
    directly on the root router, so it is never behind the wall. These tests
    drive the ASGI app with a raw websocket scope (the HTTP test client does
    not speak WebSocket).
    """

    @staticmethod
    async def _connect(app: Any) -> list[dict[str, Any]]:
        incoming: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await incoming.put({"type": "websocket.connect"})
        sent: list[dict[str, Any]] = []

        async def receive() -> dict[str, Any]:
            return await incoming.get()

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)

        scope = {
            "type": "websocket",
            "path": "/workers/connect",
            "raw_path": b"/workers/connect",
            "query_string": b"",
            "headers": [],
        }
        await asyncio.wait_for(app(scope, receive, send), timeout=2.0)
        return sent

    async def test_workers_connect_open_no_authenticator(self, app: Any) -> None:
        """Without an authenticator, the WebSocket accepts then closes."""
        sent = await self._connect(app)
        types = [m["type"] for m in sent]
        assert "websocket.accept" in types
        assert "websocket.close" in types

    async def test_workers_connect_open_with_authenticator(
        self, authed_app: Any,
    ) -> None:
        """With an authenticator configured, the WebSocket is still reachable.

        No Authorization header is presented, yet the connection succeeds --
        proof the auth wall does not gate the native WebSocket route.
        """
        sent = await self._connect(authed_app)
        types = [m["type"] for m in sent]
        assert "websocket.accept" in types
        assert "websocket.close" in types


@dataclass(frozen=True)
class _FakeRun:
    """Minimal stand-in for RunReport; the access check reads created_by."""

    created_by: UUID


class TestAguiRunAccessThroughWall:
    """The auth wall feeds auth_context into AG-UI, which then enforces
    per-run ownership. These tests drive the full compositor: real bearer
    auth wall -> AG-UI ownership check.
    """

    @staticmethod
    async def _authed_app_with_owner(
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
    ) -> tuple[Any, str, UUID]:
        """Build a compositor whose principal storage backs one consumer.

        Returns (app, bearer_token, owner_principal_id) where the token
        authenticates as the consumer whose principal is owner_principal_id.
        """
        backend = InMemoryAuthBackend()
        token = "owner-token"
        consumer_id = await backend.create_consumer(
            "agui-owner",
            TrustTier.IDENTIFIED,
            ["api"],
            consumer_id=uuid4(),
            principal_id=uuid4(),
        )
        await backend.create_credential(consumer_id, "bearer", token)

        storage = InMemoryPrincipalStorage()
        # AuthContext.consumer_id == consumer_id; resolver looks it up by ref.
        principal = await storage.mint_principal(
            KIND_CONSUMER, consumer_id, "agui-owner",
        )

        verifiers: dict[str, HashCredentialVerifier | HmacCredentialVerifier] = {
            "bearer": HashCredentialVerifier("bearer", backend),
        }
        authenticator = Authenticator(backend, verifiers)
        ctx = DispatchContext(
            pool=object(),  # sentinel: get_run is patched in the tests
            principal_storage=storage,
        )
        config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
        )
        return create_compositor(config), token, principal.id

    async def test_owner_streams_own_run(
        self, agent_card: AgentCard, skill_registry: SkillRegistry,
    ) -> None:
        app, token, owner_id = await self._authed_app_with_owner(
            agent_card, skill_registry,
        )
        run = _FakeRun(created_by=owner_id)
        with patch(
            "orxtra.agui._server.get_run", new=AsyncMock(return_value=run),
        ):
            async with AsyncTestClient(app) as client:
                async with asyncio.timeout(5), client.stream(
                    "GET",
                    "/ag-ui/events",
                    params={"run_id": str(uuid4())},
                    headers={"authorization": f"Bearer {token}"},
                ) as resp:
                    assert resp.status_code == 200

    async def test_non_owner_gets_403(
        self, agent_card: AgentCard, skill_registry: SkillRegistry,
    ) -> None:
        app, token, _owner_id = await self._authed_app_with_owner(
            agent_card, skill_registry,
        )
        run = _FakeRun(created_by=uuid4())  # a different principal owns it
        with patch(
            "orxtra.agui._server.get_run", new=AsyncMock(return_value=run),
        ):
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    "/ag-ui/events",
                    params={"run_id": str(uuid4())},
                    headers={"authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 403

    async def test_unknown_run_gets_404(
        self, agent_card: AgentCard, skill_registry: SkillRegistry,
    ) -> None:
        app, token, _owner_id = await self._authed_app_with_owner(
            agent_card, skill_registry,
        )
        with patch(
            "orxtra.agui._server.get_run", new=AsyncMock(return_value=None),
        ):
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    "/ag-ui/events",
                    params={"run_id": str(uuid4())},
                    headers={"authorization": f"Bearer {token}"},
                )
                assert resp.status_code == 404


class TestCorsPosture:
    """The compositor's CORS posture under fastware >= 0.5.0.

    fastware enables credentialed CORS and rejects the
    wildcard-with-credentials combination. The compositor therefore never
    defaults to a wildcard: with no configured origins it applies no CORS
    middleware; with configured origins it passes them through as an
    explicit credentialed allowlist; a wildcard entry is rejected up front.
    """

    def test_wildcard_origin_rejected(
        self, compositor_config: CompositorConfig,
    ) -> None:
        """A wildcard entry in cors_origins is a hard error at build time."""
        config = CompositorConfig(
            dispatch_context=compositor_config.dispatch_context,
            agent_card=compositor_config.agent_card,
            skill_registry=compositor_config.skill_registry,
            cors_origins=["*"],
        )
        with pytest.raises(ValueError, match=r"wildcard"):
            create_compositor(config)

    async def test_no_cors_headers_by_default(self, app: Any) -> None:
        """With no configured origins, cross-origin requests get no CORS headers."""
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/health", headers={"origin": "https://evil.example"},
            )
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers

    async def test_explicit_origin_echoed_with_credentials(
        self,
        dispatch_context: DispatchContext,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
    ) -> None:
        """A configured explicit origin is allowed and credentialed CORS is set."""
        config = CompositorConfig(
            dispatch_context=dispatch_context,
            agent_card=agent_card,
            skill_registry=skill_registry,
            cors_origins=["https://app.example"],
        )
        app = create_compositor(config)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/health", headers={"origin": "https://app.example"},
            )
            assert resp.status_code == 200
            assert resp.headers["access-control-allow-origin"] == "https://app.example"
            assert resp.headers["access-control-allow-credentials"] == "true"

    async def test_unlisted_origin_not_echoed(
        self,
        dispatch_context: DispatchContext,
        agent_card: AgentCard,
        skill_registry: SkillRegistry,
    ) -> None:
        """An origin outside the allowlist receives no allow-origin header."""
        config = CompositorConfig(
            dispatch_context=dispatch_context,
            agent_card=agent_card,
            skill_registry=skill_registry,
            cors_origins=["https://app.example"],
        )
        app = create_compositor(config)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/health", headers={"origin": "https://evil.example"},
            )
            assert resp.status_code == 200
            assert "access-control-allow-origin" not in resp.headers


class _LifespanRunner:
    """Minimal ASGI lifespan driver for the MCP Starlette app."""

    def __init__(self, app: Any) -> None:
        self._app = app
        self._to: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        return await self._to.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._from.put(message)

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(
            self._app({"type": "lifespan", "state": {}}, self._receive, self._send),
        )
        await self._to.put({"type": "lifespan.startup"})
        message = await self._from.get()
        assert message["type"] == "lifespan.startup.complete", message
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._to.put({"type": "lifespan.shutdown"})
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._from.get(), timeout=5)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._task


class TestMcpTransportSecurity:
    """MCP transport security via ``mcp_allowed_hosts``.

    The ``_build_mcp_app`` function explicitly sets
    ``TransportSecuritySettings`` with the three loopback port-wildcard
    patterns (``localhost:*``, ``127.0.0.1:*``, ``[::1]:*``) plus any
    caller-supplied additional hosts. These tests drive the real MCP
    transport to verify host acceptance/rejection.
    """

    async def test_configured_proxy_host_accepted(self) -> None:
        """A host listed in ``mcp_allowed_hosts`` is accepted."""
        ctx = DispatchContext()
        mcp_app = _build_mcp_app(ctx, mcp_allowed_hosts=("proxy.example.com",))
        async with (
            _LifespanRunner(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app),
                base_url="http://proxy.example.com",
            ) as client,
        ):
            # GET to the MCP endpoint: expected to reach the handler
            # (which returns 405 for GET), not be blocked by host
            # validation (which returns 421).
            resp = await client.get("/")
            assert resp.status_code != 421, (
                "a configured proxy host must not be rejected"
            )

    async def test_unconfigured_non_loopback_rejected(self) -> None:
        """A non-loopback host NOT in ``mcp_allowed_hosts`` is rejected 421."""
        ctx = DispatchContext()
        mcp_app = _build_mcp_app(ctx, mcp_allowed_hosts=())
        async with (
            _LifespanRunner(mcp_app),
            httpx.AsyncClient(
                transport=httpx.ASGITransport(app=mcp_app),
                base_url="http://evil.example.com",
            ) as client,
        ):
            resp = await client.get("/")
            assert resp.status_code == 421, (
                "an unconfigured non-loopback host must be rejected"
            )

    async def test_loopback_with_port_always_accepted(self) -> None:
        """Loopback hosts with an arbitrary port are always accepted."""
        ctx = DispatchContext()
        mcp_app = _build_mcp_app(ctx, mcp_allowed_hosts=())
        async with _LifespanRunner(mcp_app):
            for base_url in (
                "http://localhost:9090",
                "http://127.0.0.1:4567",
            ):
                async with httpx.AsyncClient(
                    transport=httpx.ASGITransport(app=mcp_app),
                    base_url=base_url,
                ) as client:
                    resp = await client.get("/")
                    assert resp.status_code != 421, (
                        f"loopback host {base_url} must not be rejected"
                    )
