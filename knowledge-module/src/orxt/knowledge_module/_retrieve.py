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

    _ = tags

    configure_cognee(config)
    try:
        import cognee  # type: ignore[import-untyped]  # noqa: PLC0415
    except ImportError:
        msg = "cognee is required for the knowledge module. Install it with: uv add cognee"
        raise RuntimeError(msg) from None

    raw_results = await cognee.search(
        query_text=query,
        query_type="GRAPH_COMPLETION",
    )

    results: list[KnowledgeResult] = []
    for item in raw_results[:max_results]:
        is_dict = isinstance(item, dict)
        text = str(item.get("text", "")) if is_dict else str(item)
        source = (
            str(item.get("source", "cognee")) if is_dict else "cognee"
        )
        permanent = (
            bool(item.get("permanent", False)) if is_dict else False
        )
        score = (
            float(item.get("score", 0.0)) if is_dict else 0.0
        )
        results.append(
            KnowledgeResult(
                text=text,
                source=source,
                permanent=permanent,
                relevance_score=score,
            )
        )

    return results
