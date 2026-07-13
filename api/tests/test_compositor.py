"""Tests for the HTTP compositor."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
)
from fastware.testing import AsyncTestClient
from orxtra.a2a._skills import SkillRegistry
from orxtra.api._compositor import CompositorConfig, create_compositor
from orxtra.auth import (
    Authenticator,
    HashCredentialVerifier,
    HmacCredentialVerifier,
    InMemoryAuthBackend,
)
from orxtra.protocols import TrustTier
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
    consumer_id = await backend.create_consumer(
        "compositor-test-user", TrustTier.VERIFIED, ["api"],
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
