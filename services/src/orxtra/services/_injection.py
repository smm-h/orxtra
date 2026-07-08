"""Refresh-callback factories for scheduler injection points.

Bridges trace readers and overseer staleness logic into the
scheduler's in-memory lists.  Lives in the composition layer
(services), which legally imports both orchestration (scheduler)
and intelligence (overseer).

The scheduler receives pre-built async callbacks and never
imports from overseer -- the layer boundary is maintained.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from orxtra.notepad import NotepadEntry
    from orxtra.trace import StorageBackend


def build_constraints_refresher(
    backend: StorageBackend,
) -> Callable[[UUID], Awaitable[list[tuple[str, str]]]]:
    """Build a callback that reads active constraints from trace.

    Returns (text, tier) tuples matching the scheduler's
    ``_active_constraints`` format.
    """

    async def _refresh(run_id: UUID) -> list[tuple[str, str]]:
        rows = await backend.read_constraints(
            run_id, active_only=True,
        )
        return [(r["text"], r["tier"]) for r in rows]

    return _refresh


def build_lessons_refresher(
    backend: StorageBackend,
    repo_dir: Path,
    relevance_tags: list[str],
) -> Callable[[UUID], Awaitable[list[dict[str, Any]]]]:
    """Build a callback that reads lessons, applies staleness check.

    Uses overseer's ``filter_stale_lessons`` to split fresh/stale.
    Returns dicts with ``text`` and ``stale`` keys, matching the
    scheduler's ``_lessons`` format consumed by LessonsProvider.
    """

    async def _refresh(
        run_id: UUID,  # noqa: ARG001
    ) -> list[dict[str, Any]]:
        from orxtra.overseer import (
            filter_stale_lessons,
        )

        raw_lessons = await backend.query_relevant_lessons(
            relevance_tags,
        )
        if not raw_lessons:
            return []

        # Normalize created_at to isoformat strings for
        # filter_stale_lessons (it parses isoformat).
        normalized: list[dict[str, Any]] = []
        for lesson in raw_lessons:
            entry: dict[str, Any] = dict(lesson)
            created = entry.get("created_at")
            if created is not None and not isinstance(
                created, str,
            ):
                entry["created_at"] = created.isoformat()
            normalized.append(entry)

        fresh, stale = await filter_stale_lessons(
            normalized, repo_dir,
        )
        result: list[dict[str, Any]] = [
            {"text": lesson["text"], "stale": False} for lesson in fresh
        ]
        result.extend({"text": lesson["text"], "stale": True} for lesson in stale)
        return result

    return _refresh


def build_notepad_refresher(
    backend: StorageBackend,
) -> Callable[[UUID], Awaitable[list[NotepadEntry]]]:
    """Build a callback that reads notepad entries from trace."""

    async def _refresh(run_id: UUID) -> list[NotepadEntry]:
        return await backend.read_notepad(run_id)

    return _refresh
