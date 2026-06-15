from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest


@pytest.fixture
def mock_pool() -> Any:
    return AsyncMock()


@pytest.fixture
def server(mock_pool: Any) -> Any:
    from orxt.mcp._server import MCPServer
    return MCPServer(mock_pool)
