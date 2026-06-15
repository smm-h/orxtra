from __future__ import annotations

# ruff: noqa: ANN401
from typing import Any
from unittest.mock import AsyncMock

import pytest
from orxt.mcp._server import MCPServer


@pytest.fixture
def mock_pool() -> Any:
    return AsyncMock()


@pytest.fixture
def server(mock_pool: Any) -> Any:
    return MCPServer(mock_pool)
