"""Streamable HTTP transport factory for the MCP server.

Creates a Starlette ASGI app via the MCP SDK's streamable HTTP support.
The returned app can be mounted in fastware or served standalone.

Lifecycle notes:
  The StreamableHTTPSessionManager used internally by FastMCP manages
  MCP session state (active sessions, pending requests). When mounting
  in fastware, the compositor should ensure the FastMCP instance's
  lifespan is wired to the host app's lifespan so sessions are
  cleaned up on shutdown. For standalone use, the Starlette app
  handles its own lifespan automatically.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orxtra.services import DispatchContext
    from starlette.types import ASGIApp


def create_app(
    dispatch_context: DispatchContext,
    mcp_allowed_hosts: tuple[str, ...] = (),
) -> ASGIApp:
    """Create a streamable HTTP ASGI app for the MCP server.

    Args:
        dispatch_context: Infrastructure dependencies for dispatching
            capability calls (pool, dispatch_backend, principal storage,
            and the authenticated caller context).
        mcp_allowed_hosts: Additional hostnames the MCP transport accepts
            beyond the loopback baseline (``localhost:*``, ``127.0.0.1:*``,
            ``[::1]:*``).  An empty tuple means loopback-only.

    Returns:
        A Starlette ASGI app that speaks the MCP streamable HTTP
        protocol. Can be mounted at a path in fastware or served
        directly with uvicorn.
    """
    from orxtra.mcp._server import MCPServer

    from mcp.server.transport_security import TransportSecuritySettings

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
    server.fastmcp.settings.transport_security = transport_security
    return server.fastmcp.streamable_http_app()
