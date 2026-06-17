from __future__ import annotations

import tomllib
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from orxt.agent import load_agents, load_categories
from orxt.protocols._task import BudgetExhaustionPolicy
from orxt.scheduler import Scheduler, load_workflow
from orxt.trace import RunReport, RunSummary, TraceWriter, read_run_report
from orxt.trace import list_runs as _list_runs
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg  # type: ignore[import-untyped]


class RunConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    workflow_path: Path
    agents_dir: Path
    knowledge_dir: Path
    categories_path: Path
    db_url: str
    provider_configs: dict[str, dict[str, str]]
    budget: Decimal
    autonomy_level: str
    budget_exhaustion_policy: BudgetExhaustionPolicy = BudgetExhaustionPolicy.UNLIMITED


def _serialize_config(config: RunConfig) -> dict[str, Any]:
    data = config.model_dump()
    data["agents_dir"] = str(config.agents_dir)
    data["knowledge_dir"] = str(config.knowledge_dir)
    data["categories_path"] = str(config.categories_path)
    data["workflow_path"] = str(config.workflow_path)
    data["budget"] = str(config.budget)
    return data


async def start_run(
    pool: asyncpg.Pool,
    intent: str,
    config: RunConfig,
    *,
    transport_registry: dict[str, Any] | None = None,
    overseer: Any | None = None,  # noqa: ANN401
) -> UUID:
    writer = TraceWriter(pool)
    run_id = await writer.create_run(
        intent, _serialize_config(config), config.autonomy_level
    )
    try:
        await writer.transition_run(run_id, "running")
        agents = load_agents(config.agents_dir)
        categories = load_categories(config.categories_path)
        registry = transport_registry if transport_registry is not None else {}
        scheduler = Scheduler(
            trace_writer=writer,
            transport_registry=registry,
            agents=agents,
            categories=categories,
            run_id=run_id,
            pool=pool,
            overseer_interface=overseer,
            knowledge_dir=config.knowledge_dir,
            budget_exhaustion_policy=config.budget_exhaustion_policy,
            budget_limit=config.budget,
            autonomy_level=config.autonomy_level,
        )
        workflow_config = load_workflow(config.workflow_path)
        await scheduler.execute_workflow(workflow_config)
        await writer.transition_run(run_id, "completed")
    except Exception:
        await writer.transition_run(run_id, "failed")
        raise
    return run_id


async def start_run_from_file(
    pool: asyncpg.Pool, intent: str, config_path: Path
) -> UUID:
    if not config_path.is_file():  # noqa: ASYNC240
        msg = f"Config file not found: {config_path}"
        raise FileNotFoundError(msg)
    with config_path.open("rb") as f:
        raw = tomllib.load(f)
    for key in ("workflow_path", "agents_dir", "knowledge_dir", "categories_path"):
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
