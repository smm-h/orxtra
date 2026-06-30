from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0"

# Lazy imports to avoid triggering the mcp SDK import at module load time.
# With pytest --import-mode=importlib, the workspace directory mcp/ can
# shadow the mcp SDK package during conftest loading. Deferring these
# imports to access time avoids the conflict.


def __getattr__(name: str) -> object:
    if name == "MCPServer":
        from orxtra.mcp._server import MCPServer
        return MCPServer
    if name == "get_tool_definitions":
        from orxtra.mcp._server import get_tool_definitions
        return get_tool_definitions
    if name == "create_app":
        from orxtra.mcp._http import create_app
        return create_app
    if name == "McpNotificationSink":
        from orxtra.mcp._notification_sink import McpNotificationSink
        return McpNotificationSink
    msg = f"module {__name__!r} has no attribute {name!r}"
    raise AttributeError(msg)


__all__ = [
    "__version__",
    "MCPServer",
    "McpNotificationSink",
    "create_app",
    "get_tool_definitions",
]
