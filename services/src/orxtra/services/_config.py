from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from orxtra.session._pricing import PRICING_TABLE

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


async def dump_config(
    pool: asyncpg.Pool, run_id: UUID
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT config_snapshot FROM runs WHERE id = $1", run_id
        )
    if row is None:
        return None
    raw: Any = row["config_snapshot"]
    result: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw)
    return result


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
