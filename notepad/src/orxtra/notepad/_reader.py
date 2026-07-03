from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.trace import read_notepad as _trace_read_notepad

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxtra.notepad._types import NotepadEntry

async def read_notepad(pool: asyncpg.Pool, run_id: UUID) -> list[NotepadEntry]:
    """Read all notepad entries for a run, ordered by created_at."""
    return await _trace_read_notepad(pool, run_id)
