"""Tests for the API lifecycle -- schema verification at startup.

Verifies that the lifespan function calls verify_schema and propagates
SchemaError if the database schema is incomplete.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest
from orxtra.api._lifecycle import ServerConfig, lifespan
from orxtra.services import SchemaError


async def test_lifespan_calls_verify_schema() -> None:
    """Lifespan calls verify_schema after creating the pool."""
    config = ServerConfig(db_url="postgresql://test:test@localhost/test", port=8080)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    with (
        patch("asyncpg.create_pool", new_callable=AsyncMock, return_value=mock_pool),
        patch(
            "orxtra.services.verify_schema",
            new_callable=AsyncMock,
        ) as mock_verify,
        patch("orxtra.a2a.SkillRegistry"),
        patch("orxtra.a2a.build_agent_card"),
        patch("orxtra.services.get_capabilities", return_value=[]),
    ):
        async with lifespan(config):
            mock_verify.assert_awaited_once_with(mock_pool)


async def test_lifespan_propagates_schema_error() -> None:
    """Lifespan propagates SchemaError from verify_schema."""
    config = ServerConfig(db_url="postgresql://test:test@localhost/test", port=8080)

    mock_pool = AsyncMock()
    mock_pool.close = AsyncMock()

    err_msg = (
        "Database schema is incomplete. "
        "Missing: tables.runs."
    )
    with (
        patch(
            "asyncpg.create_pool",
            new_callable=AsyncMock,
            return_value=mock_pool,
        ),
        patch(
            "orxtra.services.verify_schema",
            new_callable=AsyncMock,
            side_effect=SchemaError(err_msg),
        ),
        pytest.raises(SchemaError, match=r"tables\.runs"),
    ):
        async with lifespan(config):
            pass  # Should not reach here
