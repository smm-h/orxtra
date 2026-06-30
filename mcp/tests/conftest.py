from __future__ import annotations

import importlib
import importlib.util
import os
import sys
from typing import Any
from unittest.mock import AsyncMock

# Fix mcp namespace package shadow. pytest --import-mode=importlib
# registers the workspace mcp/ directory as a namespace package,
# shadowing the mcp SDK. Replace with the real SDK from site-packages.
_mcp_mod = sys.modules.get("mcp")
if _mcp_mod is not None and getattr(_mcp_mod, "__file__", None) is None:
    for _p in sys.path:
        _init = os.path.join(_p, "mcp", "__init__.py")
        if os.path.isfile(_init):
            _spec = importlib.util.spec_from_file_location(
                "mcp",
                _init,
                submodule_search_locations=[os.path.join(_p, "mcp")],
            )
            if _spec and _spec.loader:
                _real_mcp = importlib.util.module_from_spec(_spec)
                sys.modules["mcp"] = _real_mcp
                _spec.loader.exec_module(_real_mcp)
                break

import pytest
from orxtra.mcp._server import MCPServer


@pytest.fixture
def mock_pool() -> Any:  # noqa: ANN401
    return AsyncMock()


@pytest.fixture
def server(mock_pool: Any) -> MCPServer:  # noqa: ANN401
    return MCPServer(mock_pool)
