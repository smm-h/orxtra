from __future__ import annotations

from typing import Any
from uuid import UUID

from orxt.knowledge_module._config import configure_cognee
from orxt.knowledge_module._freshness import ContentHashCache
from orxt.knowledge_module._types import KnowledgeConfig

_cache = ContentHashCache()


async def ingest_lessons(config: KnowledgeConfig, lessons: list[dict[str, Any]]) -> int:
    if not lessons:
        return 0

    configure_cognee(config)
    import cognee

    changed = [
        lesson
        for lesson in lessons
        if _cache.is_changed(str(lesson.get("id", "")), str(lesson.get("content", "")))
    ]

    if not changed:
        return 0

    for lesson in changed:
        text = str(lesson.get("content", ""))
        await cognee.add(text)

    await cognee.cognify()

    for lesson in changed:
        _cache.update(str(lesson.get("id", "")), str(lesson.get("content", "")))

    return len(changed)


async def ingest_from_pool(
    config: KnowledgeConfig, pool: Any, run_id: UUID | None = None,
) -> int:
    query = "SELECT id, content, tags, permanent FROM lessons"
    params: list[Any] = []

    if run_id is not None:
        query += " WHERE run_id = $1"
        params.append(run_id)

    async with pool.acquire() as conn:
        rows = await conn.fetch(query, *params)

    lessons: list[dict[str, Any]] = [dict(row) for row in rows]
    return await ingest_lessons(config, lessons)
