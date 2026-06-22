from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from orxtra.trace._types import (
    InboxItem,
    IterationResult,
    NotepadEntry,
    RunReport,
    RunSummary,
    TaskAttempt,
    TaskSummary,
)

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    import asyncpg


def _record_to_dict(record: asyncpg.Record) -> dict[str, Any]:
    return dict(record)


async def list_tasks(pool: asyncpg.Pool, run_id: UUID) -> list[TaskSummary]:
    rows: list[asyncpg.Record] = await pool.fetch(
        "SELECT id, name, status, task_type, parent_task_id,"
        " (SELECT COUNT(*) FROM task_attempts"
        " WHERE task_id = tasks.id) AS attempt_count"
        " FROM tasks WHERE run_id = $1"
        " ORDER BY created_at",
        run_id,
    )
    return [TaskSummary.model_validate(_record_to_dict(row)) for row in rows]


async def read_task_attempt(
    pool: asyncpg.Pool, task_id: UUID, attempt: int
) -> TaskAttempt | None:
    row: asyncpg.Record | None = await pool.fetchrow(
        """
        SELECT id, task_id, attempt, status, agent_output, structured_output,
            check_result, check_verdict, session_id, input_tokens, output_tokens,
            reasoning_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
            duration_seconds
        FROM task_attempts WHERE task_id = $1 AND attempt = $2
        """,
        task_id,
        attempt,
    )
    if row is None:
        return None
    return TaskAttempt.model_validate(_record_to_dict(row))


async def read_latest_attempt(
    pool: asyncpg.Pool, task_id: UUID
) -> TaskAttempt | None:
    row: asyncpg.Record | None = await pool.fetchrow(
        """
        SELECT id, task_id, attempt, status, agent_output, structured_output,
            check_result, check_verdict, session_id, input_tokens, output_tokens,
            reasoning_tokens, cache_read_tokens, cache_write_tokens, cost_usd,
            duration_seconds
        FROM task_attempts WHERE task_id = $1
        ORDER BY attempt DESC LIMIT 1
        """,
        task_id,
    )
    if row is None:
        return None
    return TaskAttempt.model_validate(_record_to_dict(row))


async def list_iterations(
    pool: asyncpg.Pool, task_id: UUID
) -> list[IterationResult]:
    rows: list[asyncpg.Record] = await pool.fetch(
        """
        SELECT id, task_id, iteration_index, item_value, status,
            output, structured_output, check_results, started_at, finished_at
        FROM task_iterations WHERE task_id = $1
        ORDER BY iteration_index
        """,
        task_id,
    )
    return [IterationResult.model_validate(_record_to_dict(row)) for row in rows]


async def read_transcript(
    pool: asyncpg.Pool, session_id: UUID
) -> list[dict[str, Any]]:
    rows: list[asyncpg.Record] = await pool.fetch(
        """
        SELECT turn, role, content, tool_calls, tokens, created_at
        FROM transcripts WHERE session_id = $1
        ORDER BY turn
        """,
        session_id,
    )
    return [_record_to_dict(row) for row in rows]


async def search_transcript(
    pool: asyncpg.Pool, session_id: UUID, query: str
) -> list[dict[str, Any]]:
    rows: list[asyncpg.Record] = await pool.fetch(
        """
        SELECT turn, role, content, tool_calls, tokens, created_at
        FROM transcripts WHERE session_id = $1 AND content ILIKE $2
        ORDER BY turn
        """,
        session_id,
        f"%{query}%",
    )
    return [_record_to_dict(row) for row in rows]


async def read_run_report(
    pool: asyncpg.Pool, run_id: UUID
) -> RunReport | None:
    run_row: asyncpg.Record | None = await pool.fetchrow(
        "SELECT * FROM runs WHERE id = $1",
        run_id,
    )
    if run_row is None:
        return None

    run = _record_to_dict(run_row)
    tasks = await list_tasks(pool, run_id)

    decisions: list[asyncpg.Record] = await pool.fetch(
        "SELECT * FROM decisions WHERE run_id = $1 ORDER BY created_at",
        run_id,
    )
    constraints: list[asyncpg.Record] = await pool.fetch(
        "SELECT * FROM constraints WHERE run_id = $1 ORDER BY created_at",
        run_id,
    )
    assumptions: list[asyncpg.Record] = await pool.fetch(
        "SELECT * FROM assumptions WHERE run_id = $1 ORDER BY created_at",
        run_id,
    )

    return RunReport.model_validate({
        "id": run["id"],
        "intent": run["intent"],
        "status": run["status"],
        "created_at": run["created_at"],
        "finished_at": run["finished_at"],
        "autonomy_level": run["autonomy_level"],
        "config_snapshot": (
            json.loads(run["config_snapshot"])
            if isinstance(run["config_snapshot"], str)
            else run["config_snapshot"]
        ),
        "total_input_tokens": run["total_input_tokens"],
        "total_output_tokens": run["total_output_tokens"],
        "total_reasoning_tokens": run["total_reasoning_tokens"],
        "total_cache_read_tokens": run["total_cache_read_tokens"],
        "total_cache_write_tokens": run["total_cache_write_tokens"],
        "total_cost_usd": run["total_cost_usd"],
        "coherence_summary": run["coherence_summary"],
        "tasks": tasks,
        "decisions": [_record_to_dict(r) for r in decisions],
        "constraints": [_record_to_dict(r) for r in constraints],
        "assumptions": [_record_to_dict(r) for r in assumptions],
    })


async def list_runs(pool: asyncpg.Pool) -> list[RunSummary]:
    rows: list[asyncpg.Record] = await pool.fetch(
        """
        SELECT id, intent, status, created_at, finished_at
        FROM runs ORDER BY created_at DESC
        """,
    )
    return [RunSummary.model_validate(_record_to_dict(row)) for row in rows]


async def read_inbox(
    pool: asyncpg.Pool, run_id: UUID, status: str | None = None
) -> list[InboxItem]:
    if status is None:
        rows: list[asyncpg.Record] = await pool.fetch(
            "SELECT * FROM inbox_items WHERE run_id = $1 ORDER BY created_at",
            run_id,
        )
    else:
        rows = await pool.fetch(
            "SELECT * FROM inbox_items"
            " WHERE run_id = $1 AND status = $2"
            " ORDER BY created_at",
            run_id,
            status,
        )
    return [InboxItem.model_validate(_record_to_dict(row)) for row in rows]


async def read_notepad(
    pool: asyncpg.Pool, run_id: UUID
) -> list[NotepadEntry]:
    rows: list[asyncpg.Record] = await pool.fetch(
        """
        SELECT run_id, task_name, agent_name, entry_type, text, created_at
        FROM notepad_entries WHERE run_id = $1
        ORDER BY created_at
        """,
        run_id,
    )
    return [NotepadEntry.model_validate(_record_to_dict(row)) for row in rows]


async def read_active_constraints(
    pool: asyncpg.Pool, run_id: UUID,
) -> list[dict[str, Any]]:
    """Read active constraints for a run."""
    rows: list[asyncpg.Record] = await pool.fetch(
        "SELECT * FROM constraints WHERE run_id = $1",
        run_id,
    )
    return [_record_to_dict(row) for row in rows]


async def read_task_attempts(
    pool: asyncpg.Pool, task_id: UUID
) -> list[TaskAttempt]:
    """Read all attempts for a task, ordered by attempt number."""
    rows: list[asyncpg.Record] = await pool.fetch(
        "SELECT id, task_id, attempt, status, agent_output,"
        " structured_output, check_result, check_verdict,"
        " session_id, input_tokens, output_tokens,"
        " reasoning_tokens, cache_read_tokens,"
        " cache_write_tokens, cost_usd, duration_seconds"
        " FROM task_attempts WHERE task_id = $1"
        " ORDER BY attempt",
        task_id,
    )
    return [TaskAttempt.model_validate(_record_to_dict(row)) for row in rows]


async def query_events(
    pool: asyncpg.Pool,
    run_id: UUID,
    event_type: str | None = None,
    since: datetime | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Query events for a run with optional filters."""
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
    rows: list[asyncpg.Record] = await pool.fetch(query, *params)
    return [_record_to_dict(row) for row in rows]


async def read_inbox_item(
    pool: asyncpg.Pool, item_id: UUID
) -> InboxItem | None:
    """Read a single inbox item by ID."""
    row: asyncpg.Record | None = await pool.fetchrow(
        "SELECT * FROM inbox_items WHERE id = $1", item_id
    )
    if row is None:
        return None
    return InboxItem.model_validate(_record_to_dict(row))


async def read_run_config(
    pool: asyncpg.Pool, run_id: UUID
) -> dict[str, Any] | None:
    """Read the config snapshot for a run."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT config_snapshot FROM runs WHERE id = $1", run_id
        )
    if row is None:
        return None
    raw: Any = row["config_snapshot"]
    result: dict[str, Any] = json.loads(raw) if isinstance(raw, str) else dict(raw)
    return result


async def read_session_token_counts(
    pool: asyncpg.Pool, session_id: UUID
) -> list[dict[str, Any]]:
    """Read token counts from transcripts for a session."""
    rows: list[asyncpg.Record] = await pool.fetch(
        "SELECT tokens FROM transcripts"
        " WHERE session_id = $1 AND tokens IS NOT NULL",
        session_id,
    )
    return [_record_to_dict(row) for row in rows]


async def read_session_turn_count(
    pool: asyncpg.Pool, session_id: UUID
) -> int:
    """Read the number of transcript turns for a session."""
    count: Any = await pool.fetchval(
        "SELECT COUNT(*) FROM transcripts WHERE session_id = $1",
        session_id,
    )
    return count or 0


async def query_relevant_lessons(
    pool: asyncpg.Pool, tags: list[str]
) -> list[dict[str, Any]]:
    """Query lessons matching any of the given relevance tags."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, text, relevance_tags, permanent, source_file, created_at"
            " FROM lessons"
            " WHERE relevance_tags::jsonb ?| $1::text[]"
            " ORDER BY created_at DESC",
            tags,
        )
    return [_record_to_dict(row) for row in rows]


async def read_decisions(
    pool: asyncpg.Pool, run_id: UUID, limit: int = 10
) -> list[dict[str, Any]]:
    """Read decisions for a run, newest first."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, decision_type, choice, rationale, created_at"
            " FROM decisions WHERE run_id = $1"
            " ORDER BY created_at DESC LIMIT $2",
            run_id,
            limit,
        )
    return [_record_to_dict(row) for row in rows]


async def read_constraints(
    pool: asyncpg.Pool, run_id: UUID, active_only: bool = True
) -> list[dict[str, Any]]:
    """Read constraints for a run, optionally filtered to active only."""
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
    return [_record_to_dict(row) for row in rows]


async def read_assumptions(
    pool: asyncpg.Pool, run_id: UUID, status: str | None = None
) -> list[dict[str, Any]]:
    """Read assumptions for a run, optionally filtered by status."""
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
    return [_record_to_dict(row) for row in rows]


async def query_lessons(
    pool: asyncpg.Pool,
    run_id: UUID | None = None,
    tags: list[str] | None = None,
    permanent_only: bool = False,
) -> list[dict[str, Any]]:
    """Query lessons with optional filters."""
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
        "SELECT id, text, relevance_tags, permanent, source_file, created_at"  # noqa: S608
        f" FROM lessons WHERE {where}"
        " ORDER BY created_at DESC"
    )
    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)
    return [_record_to_dict(row) for row in rows]


async def read_workflow_status(
    pool: asyncpg.Pool, workflow_id: UUID
) -> dict[str, Any] | None:
    """Read overseer workflow status for a workflow."""
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT workflow_id, current_step, health, updated_at"
            " FROM overseer_workflow_status WHERE workflow_id = $1",
            workflow_id,
        )
    if row is None:
        return None
    return _record_to_dict(row)
