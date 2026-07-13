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
    secrets_env: dict[str, str] | None = None


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

    try:
        from orxtra.dispatch import PgDispatchBackend
        from orxtra.services import verify_schema

        log.info("Verifying database schema")
        await verify_schema(pool)

        # Seed the singleton system principal (idempotent via mint).
        from orxtra.identity import PgPrincipalStorage
        from orxtra.protocols import KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF

        principal_storage = PgPrincipalStorage(pool)
        await principal_storage.mint_principal(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
        )
        log.info("System principal seeded")

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

        # Resolve authenticator: explicit takes precedence, then
        # auto-construct from secrets_env if provided.
        authenticator = server_config.authenticator
        if authenticator is None and server_config.secrets_env is not None:
            authenticator = _build_authenticator(pool, server_config.secrets_env)
            log.info("Auth stack constructed from secrets_env")

        # Build incoming webhook router if authenticator is configured.
        incoming_router = None
        if authenticator is not None:
            from orxtra.incoming import create_incoming_router

            incoming_router = create_incoming_router(
                pool=pool,
                dispatch_backend=dispatch_backend,
                authenticator=authenticator,
            )
            log.info("Incoming webhook receiver mounted at /incoming")

        compositor_config = CompositorConfig(
            dispatch_context=ctx,
            agent_card=agent_card,
            skill_registry=skill_registry,
            authenticator=authenticator,
            incoming_router=incoming_router,
            cors_origins=server_config.cors_origins,
        )

        log.info("Startup complete")
        yield compositor_config

    finally:
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


def build_app(server_config: ServerConfig) -> Any:
    """Build the full ASGI app with lifespan wired in.

    Returns an ASGI callable that runs the lifespan on startup/shutdown.
    This is the entry point for serving with granian or any ASGI server.
    """
    compositor: Any = None
    lifespan_cm: Any = None

    async def app(scope: dict[str, Any], receive: Any, send: Any) -> None:
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
