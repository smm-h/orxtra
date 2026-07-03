"""Graceful lifecycle management for the compositor.

Provides an async context manager (lifespan) that creates and tears down
infrastructure dependencies: asyncpg pool, event bus, dispatch context,
auth backend, and protocol-specific resources.
"""

from __future__ import annotations

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
    import asyncpg  # noqa: PLC0415
    from orxtra.a2a import SkillRegistry, build_agent_card  # noqa: PLC0415
    from orxtra.services import DispatchContext, get_capabilities  # noqa: PLC0415

    log.info("Starting up: connecting to database")
    pool: asyncpg.Pool = await asyncpg.create_pool(server_config.db_url)

    try:
        from orxtra.dispatch import PgDispatchBackend  # noqa: PLC0415
        from orxtra.services import verify_schema  # noqa: PLC0415

        log.info("Verifying database schema")
        await verify_schema(pool)
        dispatch_backend = PgDispatchBackend(pool)
        ctx = DispatchContext(pool=pool, dispatch_backend=dispatch_backend)

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

        # Build incoming webhook router if authenticator is configured.
        incoming_router = None
        if server_config.authenticator is not None:
            from orxtra.incoming import create_incoming_router  # noqa: PLC0415

            incoming_router = create_incoming_router(
                pool=pool,
                dispatch_backend=dispatch_backend,
                authenticator=server_config.authenticator,
            )
            log.info("Incoming webhook receiver mounted at /incoming")

        compositor_config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=server_config.authenticator,
            incoming_router=incoming_router,
            cors_origins=server_config.cors_origins,
        )

        log.info("Startup complete")
        yield compositor_config

    finally:
        log.info("Shutting down: closing database pool")
        await pool.close()
        log.info("Shutdown complete")


def build_app(server_config: ServerConfig) -> Any:  # noqa: ANN401
    """Build the full ASGI app with lifespan wired in.

    Returns an ASGI callable that runs the lifespan on startup/shutdown.
    This is the entry point for serving with granian or any ASGI server.
    """
    compositor: Any = None
    lifespan_cm: Any = None

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:  # noqa: ANN401
        nonlocal compositor, lifespan_cm

        if scope["type"] == "lifespan":
            message = await receive()
            if message["type"] == "lifespan.startup":
                try:
                    lifespan_cm = lifespan(server_config)
                    compositor_config = await lifespan_cm.__aenter__()
                    compositor = create_compositor(compositor_config)
                    await send({"type": "lifespan.startup.complete"})
                except Exception:
                    log.exception("Startup failed")
                    await send({"type": "lifespan.startup.failed"})
                    return

                # Wait for shutdown
                message = await receive()
                if message["type"] == "lifespan.shutdown":
                    await lifespan_cm.__aexit__(None, None, None)
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
