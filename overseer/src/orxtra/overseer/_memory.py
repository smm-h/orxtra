from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from orxtra.trace import (
    query_lessons as _query_lessons,
    read_assumptions as _read_assumptions,
    read_constraints as _read_constraints,
    read_decisions as _read_decisions,
    read_workflow_status as _read_workflow_status,
)

if TYPE_CHECKING:
    from uuid import UUID


async def query_decisions(
    pool: Any,  # noqa: ANN401
    run_id: UUID,
    limit: int = 10,
) -> list[dict[str, Any]]:
    rows = await _read_decisions(pool, run_id, limit)
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
    rows = await _read_constraints(pool, run_id, active_only)
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
    rows = await _read_assumptions(pool, run_id, status)
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
    rows = await _query_lessons(pool, run_id, tags, permanent_only)
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
    row = await _read_workflow_status(pool, workflow_id)
    if row is None:
        return None
    return {
        "workflow_id": str(row["workflow_id"]),
        "current_step": row["current_step"],
        "health": row["health"],
        "updated_at": row["updated_at"].isoformat(),
    }
