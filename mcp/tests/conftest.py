from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest
from orxt.mcp._server import MCPServer


@pytest.fixture
def mock_pool() -> Any:  # noqa: ANN401
    return AsyncMock()


@pytest.fixture
def server(mock_pool: Any) -> Any:  # noqa: ANN401
    return MCPServer(mock_pool)
