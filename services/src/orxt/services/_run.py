from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

from orxt.trace import RunReport, RunSummary, TraceWriter, read_run_report
from orxt.trace import list_runs as _list_runs

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    agents_dir: Path
    knowledge_dir: Path
    categories_path: Path
    db_url: str
    provider_configs: dict[str, dict[str, str]]
    budget: Decimal
    autonomy_level: str


def _serialize_config(config: RunConfig) -> dict[str, Any]:
    data = config.model_dump()
    data["agents_dir"] = str(config.agents_dir)
    data["knowledge_dir"] = str(config.knowledge_dir)
    data["categories_path"] = str(config.categories_path)
    data["budget"] = str(config.budget)
    return data


async def start_run(pool: asyncpg.Pool, intent: str, config: RunConfig) -> UUID:
    writer = TraceWriter(pool)
    return await writer.create_run(
        intent, _serialize_config(config), config.autonomy_level
    )


async def start_run_from_file(
    pool: asyncpg.Pool, intent: str, config_path: Path
) -> UUID:
    if not config_path.is_file():  # noqa: ASYNC240
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)
    with config_path.open("rb") as f:
        raw = tomllib.load(f)
    for key in ("agents_dir", "knowledge_dir", "categories_path"):
        if key in raw and isinstance(raw[key], str):
            raw[key] = Path(raw[key])
    if "budget" in raw and not isinstance(raw["budget"], Decimal):
        raw["budget"] = Decimal(str(raw["budget"]))
    config = RunConfig(**raw)
    return await start_run(pool, intent, config)


async def get_run(pool: asyncpg.Pool, run_id: UUID) -> RunReport | None:
    return await read_run_report(pool, run_id)


async def list_runs(pool: asyncpg.Pool) -> list[RunSummary]:
    return await _list_runs(pool)


async def abort_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    await writer.transition_run(run_id, "aborted")


async def pause_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    await writer.transition_run(run_id, "paused")


async def resume_run(pool: asyncpg.Pool, run_id: UUID) -> None:
    writer = TraceWriter(pool)
    await writer.transition_run(run_id, "running")
