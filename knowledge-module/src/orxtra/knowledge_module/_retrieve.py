from __future__ import annotations

from orxtra.knowledge_module._cognee_import import require_cognee
from orxtra.knowledge_module._config import configure_cognee
from orxtra.knowledge_module._types import KnowledgeConfig, KnowledgeResult


async def retrieve_knowledge(
    config: KnowledgeConfig | None,
    query: str,
    tags: list[str] | None = None,
    max_results: int | None = None,
) -> list[KnowledgeResult]:
    if config is None:
        return []

    effective_max = (
        max_results if max_results is not None
        else config.max_retrieval_results
    )

    configure_cognee(config)
    cognee = require_cognee()

    raw_results = await cognee.search(
        query_text=query,
        query_type="GRAPH_COMPLETION",
    )

    results: list[KnowledgeResult] = []
    for item in raw_results:
        if len(results) >= effective_max:
            break

        is_dict = isinstance(item, dict)

        # Filter by tags if provided
        if tags and is_dict:
            item_tags = item.get("tags", [])
            if isinstance(item_tags, list) and not any(t in item_tags for t in tags):
                continue

        text = str(item.get("text", "")) if is_dict else str(item)
        source = str(item.get("source", "cognee")) if is_dict else "cognee"
        permanent = bool(item.get("permanent", False)) if is_dict else False
        score = float(item.get("score", 0.0)) if is_dict else 0.0

        results.append(
            KnowledgeResult(
                text=text,
                source=source,
                permanent=permanent,
                relevance_score=score,
            )
        )

    return results
