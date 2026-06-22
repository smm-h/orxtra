from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


async def query_decisions(
    pool: Any,  # noqa: ANN401
    run_id: UUID,
    limit: int = 10,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, decision_type, choice, rationale, created_at"
            " FROM decisions WHERE run_id = $1"
            " ORDER BY created_at DESC LIMIT $2",
            run_id,
            limit,
        )
    return [
        {
            "id": str(row["id"]),
            "decision_type": row["decision_type"],
            "choice": row["choice"],
            "rationale": row["rationale"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def query_constraints(
    pool: Any,  # noqa: ANN401
    run_id: UUID,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    if active_only:
        query = (
            "SELECT id, text, tier, active, created_at"
            " FROM constraints WHERE run_id = $1 AND active = true"
            " ORDER BY created_at DESC"
        )
    else:
        query = (
            "SELECT id, text, tier, active, created_at"
            " FROM constraints WHERE run_id = $1"
            " ORDER BY created_at DESC"
        )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, run_id)
    return [
        {
            "id": str(row["id"]),
            "text": row["text"],
            "tier": row["tier"],
            "active": row["active"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def query_assumptions(
    pool: Any,  # noqa: ANN401
    run_id: UUID,
    status: str | None = None,
) -> list[dict[str, Any]]:
    if status is not None:
        query = (
            "SELECT id, text, status, scope, inbox_item_id, created_at"
            " FROM assumptions WHERE run_id = $1 AND status = $2"
            " ORDER BY created_at DESC"
        )
        args: tuple[Any, ...] = (run_id, status)
    else:
        query = (
            "SELECT id, text, status, scope, inbox_item_id, created_at"
            " FROM assumptions WHERE run_id = $1"
            " ORDER BY created_at DESC"
        )
        args = (run_id,)
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *args)
    return [
        {
            "id": str(row["id"]),
            "text": row["text"],
            "status": row["status"],
            "scope": row["scope"],
            "inbox_item_id": (
                str(row["inbox_item_id"])
                if row["inbox_item_id"]
                else None
            ),
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def query_lessons(
    pool: Any,  # noqa: ANN401
    run_id: UUID | None = None,
    tags: list[str] | None = None,
    permanent_only: bool = False,
) -> list[dict[str, Any]]:
    conditions: list[str] = []
    params: list[Any] = []
    idx = 1

    if run_id is not None:
        conditions.append(f"run_id = ${idx}")
        params.append(run_id)
        idx += 1

    if permanent_only:
        conditions.append("permanent = true")

    if tags is not None:
        conditions.append(f"relevance_tags::jsonb ?| ${idx}::text[]")
        params.append(tags)
        idx += 1

    where = " AND ".join(conditions) if conditions else "true"
    query = (
        f"SELECT id, text, relevance_tags, permanent, source_file, created_at"  # noqa: S608
        f" FROM lessons WHERE {where}"
        f" ORDER BY created_at DESC"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [
        {
            "id": str(row["id"]),
            "text": row["text"],
            "relevance_tags": (
                json.loads(row["relevance_tags"])
                if isinstance(row["relevance_tags"], str)
                else row["relevance_tags"]
            ),
            "permanent": row["permanent"],
            "source_file": row["source_file"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def query_workflow_status(
    pool: Any,  # noqa: ANN401
    workflow_id: UUID,
) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT workflow_id, current_step, health, updated_at"
            " FROM overseer_workflow_status WHERE workflow_id = $1",
            workflow_id,
        )
    if row is None:
        return None
    return {
        "workflow_id": str(row["workflow_id"]),
        "current_step": row["current_step"],
        "health": row["health"],
        "updated_at": row["updated_at"].isoformat(),
    }
