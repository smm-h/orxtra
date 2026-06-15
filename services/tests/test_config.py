from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from conftest import FakeRecord
from orxt.services._config import dump_config, show_pricing
from orxt.session._pricing import PRICING_TABLE

if TYPE_CHECKING:
    from uuid import UUID


@pytest.mark.asyncio
async def test_dump_config_returns_snapshot(
    mock_pool: AsyncMock, mock_conn: AsyncMock, sample_run_id: UUID
) -> None:
    config_data = {"agents_dir": "/agents", "budget": "10.00"}
    record = FakeRecord({"config_snapshot": json.dumps(config_data)})
    mock_conn.fetchrow = AsyncMock(return_value=record)

    result = await dump_config(mock_pool, sample_run_id)

    assert result == config_data
    mock_conn.fetchrow.assert_called_once()


@pytest.mark.asyncio
async def test_dump_config_unknown_run(
    mock_pool: AsyncMock, mock_conn: AsyncMock, sample_run_id: UUID
) -> None:
    mock_conn.fetchrow = AsyncMock(return_value=None)

    result = await dump_config(mock_pool, sample_run_id)

    assert result is None


@pytest.mark.asyncio
async def test_show_pricing_returns_dict() -> None:
    result = await show_pricing()

    assert isinstance(result, dict)
    assert len(result) > 0
    for model_name, rates in result.items():
        assert isinstance(model_name, str)
        assert isinstance(rates, dict)
        assert "input_per_million" in rates
        assert "output_per_million" in rates
        assert "cache_read_per_million" in rates
        assert "cache_write_per_million" in rates


@pytest.mark.asyncio
async def test_show_pricing_includes_all_models() -> None:
    result = await show_pricing()

    assert set(result.keys()) == set(PRICING_TABLE.keys())
    for model_name in PRICING_TABLE:
        assert model_name in result
        rates = result[model_name]
        assert all(isinstance(v, str) for v in rates.values())
