"""HTTP compositor: mounts MCP, A2A, AG-UI, and native routes on a single ASGI app.

Authentication is enforced by orxtra's transport-level auth middleware
(``orxtra.auth.auth_middleware``), not by the protocol sub-apps themselves:
  - MCP (/mcp): auth wall wraps the mounted sub-app
  - A2A (/a2a): auth wall wraps the mounted sub-app
  - AG-UI (/ag-ui/*): auth wall wraps the mounted sub-app

The wall applies only when an ``Authenticator`` is configured on the
``CompositorConfig``. With no authenticator, the sub-apps are mounted raw
(explicit unauthenticated mode -- not a runtime fallback). The middleware
passes non-HTTP scopes (websocket, lifespan) through untouched, so mounted
sub-app lifespans and the native /workers/connect WebSocket are unaffected.
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
    mcp_allowed_hosts: tuple[str, ...] = ()
    """Additional hostnames the MCP transport accepts beyond the loopback
    baseline.  Forwarded verbatim from ``ServerConfig.mcp_allowed_hosts``.
    """
    worker_registry: Any = None
    """In-memory WorkerRegistry for brain-worker connections.

    Typed as Any to avoid importing worker at module scope; the lifespan
    passes the concrete ``WorkerRegistry`` instance.
    """


def create_compositor(config: CompositorConfig) -> Callable[..., Any]:
    """Create the root ASGI application composing all protocol servers.

    Layout:
      /mcp     -- MCP streamable HTTP (mounted sub-app)
      /a2a     -- A2A JSON-RPC (mounted sub-app)
      /ag-ui/* -- AG-UI SSE routes (native, auth-wrapped)
      /.well-known/agent.json -- A2A agent card (GET)
      /health  -- health check (GET)
      /workers/connect -- worker WebSocket (auth-wrapped sub-app)
    """
    router = Router()

    # -- Mount MCP at /mcp --
    # The MCP Starlette app has its route at streamable_http_path
    # (default "/mcp"). When mounted at /mcp, fastware strips the
    # prefix so the inner path becomes "/". We build the MCP app
    # with streamable_http_path="/" so routing works after mount.
    mcp_app = _build_mcp_app(config.dispatch_context, config.mcp_allowed_hosts)
    _mount_sub_app(router, "/mcp", mcp_app, config.authenticator)

    # -- Mount A2A at /a2a --
    # Similarly, the A2A Starlette app has its JSON-RPC route at
    # rpc_url (default "/a2a"). We build with rpc_url="/" so it
    # matches after the /a2a prefix is stripped.
    a2a_app = _build_a2a_app(
        config.dispatch_context,
        config.agent_card,
        config.skill_registry,
    )
    _mount_sub_app(router, "/a2a", a2a_app, config.authenticator)

    # -- AG-UI SSE routes (under /ag-ui) --
    from orxtra.agui import create_agui_router

    # Build the subscribe_run callback from the RunManager if available.
    _run_manager = config.dispatch_context.run_manager
    _subscribe_run_fn = _run_manager.subscribe if _run_manager is not None else None

    agui_router, _registry = create_agui_router(
        pool=config.dispatch_context.pool,
        principal_storage=config.dispatch_context.principal_storage,
        subscribe_run=_subscribe_run_fn,
    )

    # Wrap AG-UI routes with the auth wall if an authenticator is provided.
    if config.authenticator is not None:
        agui_app = create_app(agui_router)
        _mount_sub_app(router, "/ag-ui", agui_app, config.authenticator)
    else:
        router.include_router(agui_router, prefix="/ag-ui")

    # -- Notification SSE stream (under /notifications) --
    _ctx = config.dispatch_context
    if (
        _ctx.notification_port is not None
        and _ctx.event_bus is not None
        and _ctx.principal_storage is not None
    ):
        from orxtra.notification import create_notification_router

        notif_router = create_notification_router(
            notification_port=_ctx.notification_port,
            event_bus=_ctx.event_bus,
            principal_storage=_ctx.principal_storage,
        )

        if config.authenticator is not None:
            notif_app = create_app(notif_router)
            _mount_sub_app(
                router, "/notifications", notif_app, config.authenticator,
            )
        else:
            router.include_router(notif_router, prefix="/notifications")

    # -- Incoming webhook receiver (under /incoming) --
    if config.incoming_router is not None:
        router.include_router(config.incoming_router, prefix="/incoming")

    # -- Agent card at /.well-known/agent.json --
    _agent_card_json = _serialize_agent_card(config.agent_card)

    @router.get("/.well-known/agent.json")
    async def agent_card_handler(request: Any) -> dict[str, Any]:  # noqa: ARG001
        return _agent_card_json

    # -- Health check --
    @router.get("/health")
    async def health_handler(request: Any) -> dict[str, str]:  # noqa: ARG001
        return {"status": "ok"}

    # -- Workers WebSocket (under /workers, auth-wrapped) --
    # The worker WebSocket endpoint is a sub-app mounted at /workers,
    # wrapped with the auth middleware (matching the MCP/A2A/AG-UI pattern).
    # The handler receives authenticated WebSocket connections from
    # NativeWorker instances and manages the brain-worker bridge lifecycle.
    if config.worker_registry is not None:
        worker_router = _build_worker_router(config.worker_registry)
        worker_app = create_app(worker_router)
        _mount_sub_app(router, "/workers", worker_app, config.authenticator)
    else:
        # No worker registry: accept and immediately close (test/stub mode).
        @router.ws("/workers/connect")
        async def workers_ws_stub(ws: WebSocket) -> None:
            await ws.accept()
            await ws.close(code=1000)

    # -- CORS posture (fastware >= 0.5.0) --
    # fastware's create_app applies CORSMiddleware with allow_credentials=True
    # and does not expose that flag; the middleware hard-rejects the
    # wildcard-with-credentials combination (a credentialed "*" echoes any
    # request origin and would grant every site cross-origin access).
    #
    # orxtra runs in exactly two deployment modes, and neither wants a wildcard:
    #   * single-operator local -- clients are the CLI and agent processes
    #     (non-browser) or same-origin UIs, so no cross-origin preflight ever
    #     occurs and no CORS headers are needed.
    #   * reverse-proxied -- browser-facing origins are deployment-specific and
    #     unknowable at build time; the operator declares them explicitly via
    #     ``cors_origins`` (or terminates CORS at the proxy).
    #
    # Therefore the default is NO CORS middleware (cors_origins unset), and any
    # configured origins are passed through as an explicit credentialed
    # allowlist. A wildcard entry is rejected up front so the credentialed-"*"
    # footgun cannot be reintroduced through configuration.
    cors_origins = config.cors_origins
    if cors_origins is not None and "*" in cors_origins:
        msg = (
            "cors_origins must list explicit origins; the '*' wildcard is "
            "rejected because fastware enables credentialed CORS and a "
            "credentialed wildcard would grant any site cross-origin access. "
            "List the exact browser origins that need access, or leave "
            "cors_origins unset for local/reverse-proxied deployments."
        )
        raise ValueError(msg)

    # -- Build the ASGI app --
    app: Callable[..., Any] = create_app(
        router,
        cors_origins=cors_origins,
        request_id=True,
        request_timing=True,
    )
    return app


def _build_mcp_app(
    dispatch_context: DispatchContext,
    mcp_allowed_hosts: tuple[str, ...] = (),
) -> Any:
    """Build the MCP Starlette app with root-relative routing.

    When mounted at /mcp, fastware strips the prefix. The MCP app
    must use streamable_http_path="/" so the inner route matches.

    Transport security is set EXPLICITLY: the loopback baseline
    (``localhost:*``, ``127.0.0.1:*``, ``[::1]:*``) is always included,
    and any caller-supplied ``mcp_allowed_hosts`` are appended. An empty
    caller list means loopback-only (the safe default). We never rely
    on the SDK's inferred default (it could change between versions).
    """
    from mcp.server.transport_security import TransportSecuritySettings
    from orxtra.mcp import MCPServer

    # Loopback baseline -- port-wildcard patterns so localhost:8080 etc.
    # are accepted, not just bare hostnames.
    loopback_hosts = ("localhost:*", "127.0.0.1:*", "[::1]:*")
    loopback_origins = (
        "http://localhost:*",
        "http://127.0.0.1:*",
        "http://[::1]:*",
    )

    allowed_hosts = list(loopback_hosts) + list(mcp_allowed_hosts)
    transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts,
        allowed_origins=list(loopback_origins),
    )

    server = MCPServer(
        pool=dispatch_context.pool,
        dispatch_context=dispatch_context,
    )
    # Override the streamable_http_path setting before generating the app.
    server.fastmcp.settings.streamable_http_path = "/"
    server.fastmcp.settings.transport_security = transport_security
    return server.fastmcp.streamable_http_app()


def _build_a2a_app(
    dispatch_context: DispatchContext,
    agent_card: AgentCard,
    skill_registry: SkillRegistry,
) -> Any:
    """Build the A2A Starlette app with root-relative routing.

    When mounted at /a2a, fastware strips the prefix. The A2A app
    must use rpc_url="/" so the inner JSON-RPC route matches.
    """
    from orxtra.a2a import create_app as create_a2a_app

    return create_a2a_app(
        dispatch_context,
        agent_card,
        skill_registry,
        rpc_url="/",
    )


def _build_worker_router(worker_registry: Any) -> Router:
    """Build a Router with the real worker WebSocket handler.

    The handler authenticates via the auth middleware (which stores
    AuthContext in scope["state"]["auth_context"]), receives the
    WorkerRegistration message, registers the worker in the registry,
    runs the BrainWorkerBridge loops, and unregisters on disconnect.

    When mounted at /workers, the route becomes /workers/connect.
    """
    import contextlib
    import json
    import logging

    from orxtra.worker import BrainWorkerBridge, WorkerRegistration

    _log = logging.getLogger("orxtra.api.workers")

    worker_router = Router()

    @worker_router.ws("/connect")
    async def workers_ws_handler(ws: WebSocket) -> None:
        # Read auth context from the middleware (if present).
        auth_context = ws.scope.get("state", {}).get("auth_context")
        consumer_id = auth_context.consumer_id if auth_context is not None else None

        await ws.accept()

        worker_id = None
        try:
            # First message must be a WorkerRegistration.
            raw = await ws.receive_text()
            envelope: dict[str, Any] = json.loads(raw)
            data = envelope.get("data", {})
            registration = WorkerRegistration.model_validate(data)

            _log.info(
                "Worker registration: root=%s consumer=%s",
                registration.root, consumer_id,
            )

            # Register in the compositor-scoped WorkerRegistry.
            # The registry assigns the real worker_id; we create the
            # bridge with a temporary UUID and patch it after registration.
            from uuid import uuid4 as _uuid4

            bridge = BrainWorkerBridge(ws, worker_id=_uuid4())
            worker_id = worker_registry.register(
                consumer_id=consumer_id,
                root=registration.root,
                capabilities=registration.capabilities,
                bridge=bridge,
            )
            bridge._worker_id = worker_id  # noqa: SLF001

            _log.info("Worker %s registered for root %s", worker_id, registration.root)

            # Run the bridge loops until disconnect.
            bridge.start()
            # Block until the receive loop ends (worker disconnects or error).
            if bridge._receive_task is not None:  # noqa: SLF001
                await bridge._receive_task  # noqa: SLF001

        except Exception:  # noqa: BLE001 -- any error during worker connection lifecycle
            _log.exception("Worker connection error")
        finally:
            if worker_id is not None:
                worker_registry.unregister(worker_id)
                _log.info("Worker %s unregistered", worker_id)
                # Stop bridge loops (idempotent if already stopped).
                with contextlib.suppress(Exception):
                    await bridge.stop()

    return worker_router


def _mount_sub_app(
    root: Router,
    prefix: str,
    sub_app: Any,
    authenticator: Authenticator | None,
) -> None:
    """Mount a pre-built ASGI sub-app, wrapping it in the auth wall when configured.

    When an authenticator is provided, the sub-app is wrapped with the
    transport-level auth middleware before mounting -- every HTTP request
    to the prefix must present a valid credential or receive 401. The
    middleware passes non-HTTP scopes (websocket, lifespan) through
    untouched, so the sub-app's lifespan still runs.

    With no authenticator, the sub-app is mounted raw (explicit
    unauthenticated mode -- not a runtime fallback).
    """
    if authenticator is not None:
        from orxtra.auth import auth_middleware

        sub_app = auth_middleware(sub_app, authenticator)
    root.mount(prefix, sub_app)


def _serialize_agent_card(agent_card: AgentCard) -> dict[str, Any]:
    """Convert an A2A AgentCard protobuf to a JSON-serializable dict."""
    from google.protobuf.json_format import MessageToDict

    result: dict[str, Any] = MessageToDict(agent_card, preserving_proto_field_name=True)
    return result
