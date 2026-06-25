from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.session import PRICING_TABLE
from orxtra.trace import read_run_config as _read_run_config

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


async def dump_config(
    pool: asyncpg.Pool, run_id: UUID
) -> dict[str, Any] | None:
    return await _read_run_config(pool, run_id)


async def show_pricing() -> dict[str, dict[str, str]]:
    return {
        model: {
            "input_per_million": str(rates.input_per_million),
            "output_per_million": str(rates.output_per_million),
            "cache_read_per_million": str(rates.cache_read_per_million),
            "cache_write_per_million": str(rates.cache_write_per_million),
        }
        for model, rates in PRICING_TABLE.items()
    }
