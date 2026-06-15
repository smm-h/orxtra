from __future__ import annotations

from typing import TYPE_CHECKING

from orxt.trace import read_notepad as _trace_read_notepad

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxt.notepad._types import NotepadEntry

_ENTRY_TYPES = ("learning", "decision", "issue")
_TYPE_HEADERS = {
    "learning": "Learnings",
    "decision": "Decisions",
    "issue": "Issues",
}


async def read_notepad(pool: asyncpg.Pool, run_id: UUID) -> list[NotepadEntry]:
    """Read all notepad entries for a run, ordered by created_at."""
    return await _trace_read_notepad(pool, run_id)


def format_notepad(entries: list[NotepadEntry]) -> str:
    """Format entries as markdown grouped by type for injection into agent prompts."""
    groups: dict[str, list[NotepadEntry]] = {t: [] for t in _ENTRY_TYPES}
    for entry in entries:
        groups[entry.entry_type].append(entry)

    sections: list[str] = ["## Context from previous steps"]
    for entry_type in _ENTRY_TYPES:
        header = _TYPE_HEADERS[entry_type]
        sections.append(f"\n### {header}")
        group = groups[entry_type]
        if group:
            sections.extend(
                f"- [{e.task_name}/{e.agent_name}] {e.text}" for e in group
            )
        else:
            sections.append("- (none)")

    return "\n".join(sections) + "\n"
