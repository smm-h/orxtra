from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxt.trace import NotepadEntry, TaskAttempt, TaskSummary
from orxt.trace import list_tasks as _list_tasks
from orxt.trace import read_notepad as _read_notepad
from orxt.trace import read_transcript as _read_transcript
from orxt.trace import search_transcript as _search_transcript

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    import asyncpg  # type: ignore[import-untyped]


async def list_tasks(
    pool: asyncpg.Pool, run_id: UUID
) -> list[TaskSummary]:
    return await _list_tasks(pool, run_id)


async def get_task_attempts(
    pool: asyncpg.Pool, task_id: UUID
) -> list[TaskAttempt]:
    rows = await pool.fetch(
        "SELECT id, task_id, attempt, status, agent_output,"
        " structured_output, check_result, check_verdict,"
        " session_id, input_tokens, output_tokens,"
        " reasoning_tokens, cache_read_tokens,"
        " cache_write_tokens, cost_usd, duration_seconds"
        " FROM task_attempts WHERE task_id = $1"
        " ORDER BY attempt",
        task_id,
    )
    return [TaskAttempt.model_validate(dict(row)) for row in rows]


async def get_transcript(
    pool: asyncpg.Pool, session_id: UUID
) -> list[dict[str, Any]]:
    return await _read_transcript(pool, session_id)


async def search_transcript(
    pool: asyncpg.Pool, session_id: UUID, query: str
) -> list[dict[str, Any]]:
    return await _search_transcript(pool, session_id, query)


async def query_events(
    pool: asyncpg.Pool,
    run_id: UUID,
    event_type: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    conditions = ["run_id = $1"]
    params: list[Any] = [run_id]
    idx = 2

    if event_type is not None:
        conditions.append(f"event_type = ${idx}")
        params.append(event_type)
        idx += 1

    if since is not None:
        conditions.append(f"created_at >= ${idx}")
        params.append(since)
        idx += 1

    params.append(limit)
    query = (
        "SELECT id, run_id, task_id, event_type, data, created_at"  # noqa: S608
        " FROM events"
        f" WHERE {' AND '.join(conditions)}"
        f" ORDER BY created_at LIMIT ${idx}"
    )
    rows = await pool.fetch(query, *params)
    return [dict(row) for row in rows]


async def get_notepad(
    pool: asyncpg.Pool, run_id: UUID
) -> list[NotepadEntry]:
    return await _read_notepad(pool, run_id)
