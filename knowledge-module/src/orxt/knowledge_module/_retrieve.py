from __future__ import annotations

from orxt.knowledge_module._config import configure_cognee
from orxt.knowledge_module._types import KnowledgeConfig, KnowledgeResult


async def retrieve_knowledge(
    config: KnowledgeConfig | None,
    query: str,
    tags: list[str] | None = None,
    max_results: int = 10,
) -> list[KnowledgeResult]:
    if config is None:
        return []

    configure_cognee(config)
    import cognee

    raw_results = await cognee.search(
        query_text=query,
        query_type="GRAPH_COMPLETION",
    )

    results: list[KnowledgeResult] = []
    for item in raw_results[:max_results]:
        text = str(item.get("text", "")) if isinstance(item, dict) else str(item)
        source = str(item.get("source", "cognee")) if isinstance(item, dict) else "cognee"
        permanent = bool(item.get("permanent", False)) if isinstance(item, dict) else False
        score = float(item.get("score", 0.0)) if isinstance(item, dict) else 0.0
        results.append(
            KnowledgeResult(
                text=text,
                source=source,
                permanent=permanent,
                relevance_score=score,
            )
        )

    return results
