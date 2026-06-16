from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID


async def query_relevant_lessons(
    pool: Any,  # noqa: ANN401
    run_id: UUID,  # noqa: ARG001
    tags: list[str],
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, text, relevance_tags, permanent, source_file, created_at"
            " FROM lessons"
            " WHERE relevance_tags::jsonb ?| $1::text[]"
            " ORDER BY created_at DESC",
            tags,
        )
    return [
        {
            "id": str(row["id"]),
            "text": row["text"],
            "relevance_tags": row["relevance_tags"],
            "permanent": row["permanent"],
            "source_file": row["source_file"],
            "created_at": row["created_at"].isoformat(),
        }
        for row in rows
    ]


async def check_staleness(
    lesson_source_files: list[str],
    repo_dir: Path,
    created_at: str,
) -> bool:
    """Check if any source file was modified after the lesson was created."""
    if not lesson_source_files:
        return False
    lesson_date = datetime.fromisoformat(created_at)
    if lesson_date.tzinfo is None:
        lesson_date = lesson_date.replace(tzinfo=UTC)
    for source_file in lesson_source_files:
        source_path = Path(source_file)
        if not source_path.is_absolute():
            source_path = repo_dir / source_path
        if not source_path.exists():
            return True
        try:
            proc = await asyncio.create_subprocess_exec(
                "git", "log", "-1", "--format=%aI", "--", str(source_path),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=repo_dir,
            )
            stdout, _ = await proc.communicate()
        except OSError:
            return False
        if proc.returncode != 0:
            return False
        git_date_str = stdout.decode().strip()
        if not git_date_str:
            continue
        git_date = datetime.fromisoformat(git_date_str)
        if git_date > lesson_date:
            return True
    return False


async def filter_stale_lessons(
    lessons: list[dict[str, Any]],
    repo_dir: Path,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Split lessons into (fresh, stale) based on source file changes."""
    fresh: list[dict[str, Any]] = []
    stale: list[dict[str, Any]] = []
    for lesson in lessons:
        source_file: str | None = lesson.get("source_file")
        if source_file is None:
            fresh.append(lesson)
            continue
        is_stale = await check_staleness(
            [source_file], repo_dir, lesson["created_at"],
        )
        if is_stale:
            stale.append(lesson)
        else:
            fresh.append(lesson)
    return fresh, stale
