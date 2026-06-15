from __future__ import annotations

import subprocess
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
    pool: Any,  # noqa: ANN401
    lesson_id: UUID,
    repo_dir: Path,
) -> bool:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT source_file, created_at FROM lessons WHERE id = $1",
            lesson_id,
        )
    if row is None:
        return True
    source_file: str | None = row["source_file"]
    if source_file is None:
        return False
    source_path = Path(source_file)
    if not source_path.is_absolute():
        source_path = repo_dir / source_path
    if not source_path.exists():
        return True
    try:
        result = subprocess.run(  # noqa: S603, ASYNC221
            ["git", "log", "-1", "--format=%aI", "--", str(source_path)],  # noqa: S607
            capture_output=True,
            text=True,
            check=True,
            cwd=repo_dir,
        )
    except subprocess.CalledProcessError:
        return True
    git_date_str = result.stdout.strip()
    if not git_date_str:
        return False
    from datetime import UTC, datetime  # noqa: PLC0415

    git_date = datetime.fromisoformat(git_date_str)
    lesson_date: datetime = row["created_at"]
    if lesson_date.tzinfo is None:
        lesson_date = lesson_date.replace(tzinfo=UTC)
    return bool(git_date > lesson_date)
