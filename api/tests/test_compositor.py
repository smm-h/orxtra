"""Tests for the HTTP compositor."""

from __future__ import annotations

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
from orxtra.auth import Authenticator, InMemoryAuthBackend
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
def app(compositor_config: CompositorConfig) -> Any:  # noqa: ANN401
    return create_compositor(compositor_config)


# -- Tests --


class TestHealthEndpoint:
    async def test_returns_200(self, app: Any) -> None:  # noqa: ANN401
        async with AsyncTestClient(app) as client:
            resp = await client.get("/health")
            assert resp.status_code == 200

    async def test_returns_ok_status(self, app: Any) -> None:  # noqa: ANN401
        async with AsyncTestClient(app) as client:
            resp = await client.get("/health")
            data = resp.json()
            assert data["status"] == "ok"


class TestAgentCardEndpoint:
    async def test_returns_200(self, app: Any) -> None:  # noqa: ANN401
        async with AsyncTestClient(app) as client:
            resp = await client.get("/.well-known/agent.json")
            assert resp.status_code == 200

    async def test_returns_valid_json(self, app: Any) -> None:  # noqa: ANN401
        async with AsyncTestClient(app) as client:
            resp = await client.get("/.well-known/agent.json")
            data = resp.json()
            assert data["name"] == "test-agent"
            assert data["version"] == "0.0.1"

    async def test_contains_capabilities(self, app: Any) -> None:  # noqa: ANN401
        async with AsyncTestClient(app) as client:
            resp = await client.get("/.well-known/agent.json")
            data = resp.json()
            assert "capabilities" in data
            assert data["capabilities"]["streaming"] is True


class TestMcpMount:
    async def test_mcp_path_is_mounted(self, app: Any) -> None:  # noqa: ANN401
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
    async def test_a2a_agent_card_accessible(self, app: Any) -> None:  # noqa: ANN401
        """Verify the A2A agent card route at /a2a/.well-known/agent-card.json."""
        async with AsyncTestClient(app) as client:
            # The A2A SDK uses agent-card.json (with hyphen), not agent.json
            resp = await client.get("/a2a/.well-known/agent-card.json")
            assert resp.status_code == 200

    async def test_a2a_jsonrpc_endpoint(self, app: Any) -> None:  # noqa: ANN401
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
        authenticator = Authenticator(backend)
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
