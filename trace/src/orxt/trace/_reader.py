from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxt.trace._types import (
    InboxItem,
    IterationResult,
    NotepadEntry,
    RunReport,
    RunSummary,
    TaskAttempt,
    TaskSummary,
)

if TYPE_CHECKING:
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
        "config_snapshot": run["config_snapshot"],
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
