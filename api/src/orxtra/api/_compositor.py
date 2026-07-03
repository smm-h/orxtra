"""HTTP compositor: mounts MCP, A2A, AG-UI, and native routes on a single ASGI app.

Each protocol handles its own authentication:
  - MCP: SDK's TransportSecuritySettings (inside the mounted app)
  - A2A: Agent Card security schemes (inside the mounted app)
  - Native routes (/ag-ui/*, /workers/*): orxtra auth middleware
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from fastware import Router, WebSocket, create_app

if TYPE_CHECKING:
    from collections.abc import Callable

    from a2a.types.a2a_pb2 import AgentCard
    from orxtra.a2a._skills import SkillRegistry
    from orxtra.auth._authenticator import Authenticator
    from orxtra.services._dispatcher import DispatchContext


@dataclass(frozen=True)
class CompositorConfig:
    """Configuration for the HTTP compositor."""

    dispatch_context: DispatchContext
    agent_card: AgentCard
    skill_registry: SkillRegistry
    authenticator: Authenticator | None = None
    incoming_router: Router | None = None
    cors_origins: list[str] | None = None


def create_compositor(config: CompositorConfig) -> Callable[..., Any]:
    """Create the root ASGI application composing all protocol servers.

    Layout:
      /mcp     -- MCP streamable HTTP (mounted sub-app)
      /a2a     -- A2A JSON-RPC (mounted sub-app)
      /ag-ui/* -- AG-UI SSE routes (native, auth-wrapped)
      /.well-known/agent.json -- A2A agent card (GET)
      /health  -- health check (GET)
      /workers/connect -- WebSocket placeholder for Phase 9
    """
    router = Router()

    # -- Mount MCP at /mcp --
    # The MCP Starlette app has its route at streamable_http_path
    # (default "/mcp"). When mounted at /mcp, fastware strips the
    # prefix so the inner path becomes "/". We build the MCP app
    # with streamable_http_path="/" so routing works after mount.
    mcp_app = _build_mcp_app(config.dispatch_context)
    router.mount("/mcp", mcp_app)

    # -- Mount A2A at /a2a --
    # Similarly, the A2A Starlette app has its JSON-RPC route at
    # rpc_url (default "/a2a"). We build with rpc_url="/" so it
    # matches after the /a2a prefix is stripped.
    a2a_app = _build_a2a_app(
        config.dispatch_context,
        config.agent_card,
        config.skill_registry,
    )
    router.mount("/a2a", a2a_app)

    # -- AG-UI SSE routes (native, under /ag-ui) --
    from orxtra.agui import create_agui_router  # noqa: PLC0415

    agui_router, _broadcaster = create_agui_router()

    # Wrap AG-UI routes with auth if an authenticator is provided.
    if config.authenticator is not None:
        _mount_authenticated_agui(router, agui_router, config.authenticator)
    else:
        router.include_router(agui_router, prefix="/ag-ui")

    # -- Incoming webhook receiver (under /incoming) --
    if config.incoming_router is not None:
        router.include_router(config.incoming_router, prefix="/incoming")

    # -- Agent card at /.well-known/agent.json --
    _agent_card_json = _serialize_agent_card(config.agent_card)

    @router.get("/.well-known/agent.json")
    async def agent_card_handler(request: Any) -> dict[str, Any]:  # noqa: ANN401, ARG001
        return _agent_card_json

    # -- Health check --
    @router.get("/health")
    async def health_handler(request: Any) -> dict[str, str]:  # noqa: ANN401, ARG001
        return {"status": "ok"}

    # -- Workers WebSocket placeholder (Phase 9) --
    @router.ws("/workers/connect")
    async def workers_ws_handler(ws: WebSocket) -> None:
        await ws.accept()
        await ws.close(code=1000)

    # -- Build the ASGI app --
    app: Callable[..., Any] = create_app(
        router,
        cors_origins=config.cors_origins or ["*"],
        request_id=True,
        request_timing=True,
    )
    return app


def _build_mcp_app(dispatch_context: DispatchContext) -> Any:  # noqa: ANN401
    """Build the MCP Starlette app with root-relative routing.

    When mounted at /mcp, fastware strips the prefix. The MCP app
    must use streamable_http_path="/" so the inner route matches.
    """
    from orxtra.mcp._server import MCPServer  # noqa: PLC0415

    server = MCPServer(
        pool=dispatch_context.pool,
        event_bus=dispatch_context.event_bus,
        dispatch_context=dispatch_context,
    )
    # Override the streamable_http_path setting before generating the app.
    server.fastmcp.settings.streamable_http_path = "/"
    return server.fastmcp.streamable_http_app()


def _build_a2a_app(
    dispatch_context: DispatchContext,
    agent_card: AgentCard,
    skill_registry: SkillRegistry,
) -> Any:  # noqa: ANN401
    """Build the A2A Starlette app with root-relative routing.

    When mounted at /a2a, fastware strips the prefix. The A2A app
    must use rpc_url="/" so the inner JSON-RPC route matches.
    """
    from orxtra.a2a import create_app as create_a2a_app  # noqa: PLC0415

    return create_a2a_app(
        dispatch_context,
        agent_card,
        skill_registry,
        rpc_url="/",
    )


def _mount_authenticated_agui(
    root: Router,
    agui_router: Router,
    authenticator: Authenticator,
) -> None:
    """Mount AG-UI routes with auth middleware applied.

    Creates a sub-app from the AG-UI router, wraps it with the auth
    middleware, and mounts it at /ag-ui on the root router.
    """
    from orxtra.auth import auth_middleware  # noqa: PLC0415

    agui_app = create_app(agui_router)
    authed_agui = auth_middleware(agui_app, authenticator)
    root.mount("/ag-ui", authed_agui)


def _serialize_agent_card(agent_card: AgentCard) -> dict[str, Any]:
    """Convert an A2A AgentCard protobuf to a JSON-serializable dict."""
    from google.protobuf.json_format import MessageToDict  # noqa: PLC0415

    result: dict[str, Any] = MessageToDict(agent_card, preserving_proto_field_name=True)
    return result
