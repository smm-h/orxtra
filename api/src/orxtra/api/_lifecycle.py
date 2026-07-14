"""Graceful lifecycle management for the compositor.

Provides an async context manager (lifespan) that creates and tears down
infrastructure dependencies: asyncpg pool, event bus, dispatch context,
auth backend, and protocol-specific resources.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from orxtra.api._compositor import CompositorConfig, create_compositor

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from orxtra.auth import Authenticator

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ServerConfig:
    """Configuration for the API server."""

    db_url: str
    port: int
    host: str = "0.0.0.0"  # noqa: S104
    cors_origins: list[str] | None = None
    agent_name: str = "orxtra"
    agent_url: str | None = None
    authenticator: Authenticator | None = None
    secrets_env: dict[str, str] | None = None
    principal_kinds: tuple[str, ...] = ()
    """App-declared principal kinds registered in addition to the built-ins.

    An empty tuple (the default) means built-in kinds only. Apps that mint
    their own principal kinds (e.g. ``"user"``) declare them here so the
    service-layer ``KindRegistry`` accepts them.
    """

    mcp_allowed_hosts: tuple[str, ...] = ()
    """Additional hostnames the MCP transport accepts beyond the loopback
    baseline (``localhost:*``, ``127.0.0.1:*``, ``[::1]:*``).

    Deploy behind a reverse proxy requires listing the proxy's Host header
    value here (e.g. ``("api.example.com",)``). An empty tuple (the default)
    means loopback-only -- the safe default for single-operator local use.
    """


@asynccontextmanager
async def lifespan(
    server_config: ServerConfig,
) -> AsyncGenerator[CompositorConfig, None]:
    """Async context manager that sets up and tears down all infrastructure.

    Yields a CompositorConfig ready for create_compositor().

    Startup:
      1. Create asyncpg pool
      2. Create DispatchContext
      3. Build A2A agent card and skill registry
      4. Optionally create auth backend + authenticator

    Shutdown:
      1. Close pool
    """
    import asyncpg
    from orxtra.a2a import SkillRegistry, build_agent_card
    from orxtra.services import DispatchContext, get_capabilities

    log.info("Starting up: connecting to database")
    pool: asyncpg.Pool = await asyncpg.create_pool(server_config.db_url)
    event_bus: Any = None

    try:
        from orxtra.dispatch import PgDispatchBackend
        from orxtra.services import verify_schema
        from orxtra.trace import PgEventBus

        log.info("Verifying database schema")
        await verify_schema(pool)

        # Seed the singleton system principal (idempotent via mint).
        from orxtra.identity import KindRegistry, PgPrincipalStorage
        from orxtra.protocols import KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF

        principal_storage = PgPrincipalStorage(pool)
        await principal_storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        log.info("System principal seeded")

        kind_registry = KindRegistry(server_config.principal_kinds)

        event_bus = PgEventBus(pool)

        from orxtra.notification import PgNotificationBackend

        notification_backend = PgNotificationBackend(pool)

        dispatch_backend = PgDispatchBackend(pool)
        ctx = DispatchContext(
            pool=pool,
            dispatch_backend=dispatch_backend,
            event_bus=event_bus,
            principal_storage=principal_storage,
            kind_registry=kind_registry,
            notification_port=notification_backend,
        )

        # Build skill registry and agent card.
        capabilities = get_capabilities()
        skill_registry = SkillRegistry(capabilities)

        agent_url = server_config.agent_url or (
            f"http://{server_config.host}:{server_config.port}/a2a"
        )
        agent_card = build_agent_card(
            skill_registry,
            url=agent_url,
            version="0.7.0",
            name=server_config.agent_name,
        )

        # Resolve authenticator: explicit takes precedence, then
        # auto-construct from secrets_env if provided.
        authenticator = server_config.authenticator
        if authenticator is None and server_config.secrets_env is not None:
            authenticator = _build_authenticator(pool, server_config.secrets_env)
            log.info("Auth stack constructed from secrets_env")

        # Build incoming webhook router if authenticator is configured.
        incoming_router = None
        if authenticator is not None:
            from orxtra.identity import PgPrincipalStorage
            from orxtra.incoming import create_incoming_router

            incoming_router = create_incoming_router(
                pool=pool,
                dispatch_backend=dispatch_backend,
                authenticator=authenticator,
                principal_storage=PgPrincipalStorage(pool),
                event_bus=event_bus,
            )
            log.info("Incoming webhook receiver mounted at /incoming")

        compositor_config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
            incoming_router=incoming_router,
            cors_origins=server_config.cors_origins,
            mcp_allowed_hosts=server_config.mcp_allowed_hosts,
        )

        log.info("Startup complete")
        yield compositor_config

    finally:
        if event_bus is not None:
            log.info("Shutting down: closing event bus")
            await event_bus.close()
        log.info("Shutting down: closing database pool")
        await pool.close()
        log.info("Shutdown complete")


def _build_authenticator(
    pool: Any,
    secrets_env: dict[str, str],
) -> Authenticator:
    """Construct the full auth stack from a secrets env mapping.

    Creates SecretRegistry, EnvMacProvider, AuthBackend, verifiers,
    and assembles them into an Authenticator.
    """
    from orxtra.auth import (
        AuthBackend,
        Authenticator,
        HashCredentialVerifier,
        HmacCredentialVerifier,
    )
    from orxtra.secrets import EnvMacProvider, create_secret_registry

    registry = create_secret_registry(secrets_env)
    mac_provider = EnvMacProvider(registry)
    auth_backend = AuthBackend(pool)
    verifiers: dict[str, HashCredentialVerifier | HmacCredentialVerifier] = {
        "bearer": HashCredentialVerifier("bearer", auth_backend),
        "api_key": HashCredentialVerifier("api_key", auth_backend),
        "hmac": HmacCredentialVerifier(mac_provider, auth_backend),
    }
    return Authenticator(auth_backend, verifiers)


async def _teardown(
    compositor_lifespan_task: asyncio.Task[None] | None,
    to_compositor: asyncio.Queue[dict[str, Any]] | None,
    from_compositor: asyncio.Queue[dict[str, Any]] | None,
    lifespan_cm: Any,
    *,
    lifespan_entered: bool,
) -> None:
    """Clean-shutdown helper: cancel compositor task, then exit the infra CM.

    Called from both failed-startup branches and the normal shutdown path.
    The order is important: the compositor task must be cancelled BEFORE
    the infrastructure context manager exits, because the compositor holds
    references to the pool and other infra resources.

    ``lifespan_entered`` tracks whether ``lifespan_cm.__aenter__()``
    completed successfully. A failure inside ``__aenter__`` (e.g.
    ``verify_schema``) already closes the pool via the CM's own ``finally``,
    so calling ``__aexit__`` on an un-entered CM raises
    "generator didn't yield."
    """
    # 1. Ask the compositor to shut down (if it started).
    if (
        compositor_lifespan_task is not None
        and to_compositor is not None
        and from_compositor is not None
    ):
        await to_compositor.put({"type": "lifespan.shutdown"})
        with contextlib.suppress(Exception):
            await asyncio.wait_for(from_compositor.get(), timeout=5)

    # 2. Cancel the compositor lifespan task.
    if compositor_lifespan_task is not None:
        compositor_lifespan_task.cancel()
        with contextlib.suppress(Exception, asyncio.CancelledError):
            await compositor_lifespan_task

    # 3. Exit the infrastructure context manager (closes pool etc.).
    if lifespan_cm is not None and lifespan_entered:
        await lifespan_cm.__aexit__(None, None, None)


def build_app(server_config: ServerConfig) -> Any:
    """Build the full ASGI app with lifespan wired in.

    Returns an ASGI callable that runs the lifespan on startup/shutdown.
    This is the entry point for serving with granian or any ASGI server.
    """
    compositor: Any = None
    lifespan_cm: Any = None
    # Background task that runs the compositor's OWN ASGI lifespan. fastware
    # (>= 0.5.0) forwards startup/shutdown from a composited app to its mounted
    # sub-apps, and the MCP StreamableHTTP session manager only initializes its
    # task group inside that lifespan. build_app builds infrastructure first
    # (pool, dispatch context) via ``lifespan()`` -- the compositor cannot be
    # constructed before that -- then drives the compositor's lifespan here so
    # the mount task groups come up and stay open while requests are served.
    compositor_lifespan_task: asyncio.Task[None] | None = None

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
        nonlocal compositor, lifespan_cm, compositor_lifespan_task

        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] != "lifespan.startup":
                return

            # Queues bridging this driver to the compositor's ASGI lifespan.
            to_compositor: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            from_compositor: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
            lifespan_entered = False

            async def _compositor_receive() -> dict[str, Any]:
                return await to_compositor.get()

            async def _compositor_send(msg: dict[str, Any]) -> None:
                await from_compositor.put(msg)

            try:
                lifespan_cm = lifespan(server_config)
                compositor_config = await lifespan_cm.__aenter__()
                lifespan_entered = True
                compositor = create_compositor(compositor_config)

                # Drive the compositor's lifespan on a long-lived background
                # task so fastware forwards startup to the mounted sub-apps
                # (initializing the MCP session-manager task group) and keeps
                # those task groups open for the server's lifetime.
                compositor_lifespan_task = asyncio.create_task(
                    compositor(
                        {"type": "lifespan", "state": {}},
                        _compositor_receive,
                        _compositor_send,
                    ),
                )
                await to_compositor.put({"type": "lifespan.startup"})
                startup_msg = await from_compositor.get()
            except Exception:
                log.exception("Startup failed")
                await _teardown(
                    compositor_lifespan_task,
                    to_compositor,
                    from_compositor,
                    lifespan_cm,
                    lifespan_entered=lifespan_entered,
                )
                await send({"type": "lifespan.startup.failed"})
                return

            if startup_msg["type"] != "lifespan.startup.complete":
                log.error(
                    "Compositor lifespan startup failed: %s",
                    startup_msg.get("message", ""),
                )
                await _teardown(
                    compositor_lifespan_task,
                    to_compositor,
                    from_compositor,
                    lifespan_cm,
                    lifespan_entered=lifespan_entered,
                )
                await send({"type": "lifespan.startup.failed"})
                return

            await send({"type": "lifespan.startup.complete"})

            # Wait for shutdown.
            message = await receive()
            if message["type"] == "lifespan.shutdown":
                await _teardown(
                    compositor_lifespan_task,
                    to_compositor,
                    from_compositor,
                    lifespan_cm,
                    lifespan_entered=lifespan_entered,
                )
                await send({"type": "lifespan.shutdown.complete"})
            return

        if compositor is None:
            # Not yet started -- reject the request.
            if scope["type"] == "http":
                await send({
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [[b"content-type", b"text/plain"]],
                })
                await send({
                    "type": "http.response.body",
                    "body": b"Service starting up",
                })
            return

        await compositor(scope, receive, send)

    return app
