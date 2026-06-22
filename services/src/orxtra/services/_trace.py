from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.trace import NotepadEntry, TaskAttempt, TaskSummary
from orxtra.trace import list_tasks as _list_tasks
from orxtra.trace import query_events as _query_events
from orxtra.trace import read_notepad as _read_notepad
from orxtra.trace import read_task_attempts as _read_task_attempts
from orxtra.trace import read_transcript as _read_transcript
from orxtra.trace import search_transcript as _search_transcript

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    import asyncpg


async def list_tasks(
    pool: asyncpg.Pool, run_id: UUID
) -> list[TaskSummary]:
    return await _list_tasks(pool, run_id)


async def get_task_attempts(
    pool: asyncpg.Pool, task_id: UUID
) -> list[TaskAttempt]:
    return await _read_task_attempts(pool, task_id)


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
    return await _query_events(pool, run_id, event_type, since, limit)


async def get_notepad(
    pool: asyncpg.Pool, run_id: UUID
) -> list[NotepadEntry]:
    return await _read_notepad(pool, run_id)
