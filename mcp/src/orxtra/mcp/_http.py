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
    from starlette.types import ASGIApp

    from orxtra.services import DispatchContext


def create_app(dispatch_context: DispatchContext) -> ASGIApp:
    """Create a streamable HTTP ASGI app for the MCP server.

    Args:
        dispatch_context: Infrastructure dependencies for dispatching
            capability calls (pool, dispatch_backend, event_bus).

    Returns:
        A Starlette ASGI app that speaks the MCP streamable HTTP
        protocol. Can be mounted at a path in fastware or served
        directly with uvicorn.
    """
    from orxtra.mcp._server import MCPServer

    server = MCPServer(
        pool=dispatch_context.pool,
        event_bus=dispatch_context.event_bus,
        dispatch_context=dispatch_context,
    )
    return server.fastmcp.streamable_http_app()
