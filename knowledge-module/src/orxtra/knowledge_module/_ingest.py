from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.knowledge_module._cognee_import import require_cognee
from orxtra.knowledge_module._config import configure_cognee
from orxtra.knowledge_module._freshness import ContentHashCache

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.knowledge_module._types import KnowledgeConfig

_cache: ContentHashCache | None = None


async def ingest_lessons(
    config: KnowledgeConfig, lessons: list[dict[str, Any]], pool: Any,  # noqa: ANN401
) -> int:
    global _cache  # noqa: PLW0603
    if _cache is None:
        _cache = ContentHashCache(pool)

    if not lessons:
        return 0

    configure_cognee(config)
    cognee = require_cognee()

    changed: list[dict[str, Any]] = []
    for lesson in lessons:
        key = str(lesson.get("id", ""))
        content = str(lesson.get("content", ""))
        if await _cache.is_changed(key, content):
            changed.append(lesson)

    if not changed:
        return 0

    for lesson in changed:
        text = str(lesson.get("content", ""))
        await cognee.add(text)

    await cognee.cognify()

    for lesson in changed:
        await _cache.update(
            str(lesson.get("id", "")), str(lesson.get("content", "")),
        )

    return len(changed)


async def ingest_from_pool(
    config: KnowledgeConfig, pool: Any, run_id: UUID | None = None,  # noqa: ANN401
) -> int:
    query = "SELECT id, content, tags, permanent FROM lessons"
    params: list[Any] = []

    if run_id is not None:
        query += " WHERE run_id = $1"
        params.append(run_id)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    lessons: list[dict[str, Any]] = [dict(row) for row in rows]
    return await ingest_lessons(config, lessons, pool)
