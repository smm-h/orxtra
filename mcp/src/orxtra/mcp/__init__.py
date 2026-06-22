from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-mcp")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.mcp._server import MCPServer
from orxtra.mcp._tools import get_tool_definitions

__all__ = [
    "__version__",
    "MCPServer",
    "get_tool_definitions",
]
