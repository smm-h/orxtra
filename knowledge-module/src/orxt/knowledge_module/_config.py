from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orxt.knowledge_module._types import KnowledgeConfig


def configure_cognee(config: KnowledgeConfig) -> None:
    import cognee  # noqa: PLC0415  # type: ignore[import-untyped]

    cognee.config.set_llm_config(
        {
            "llm_api_key": config.cognify_api_key,
            "model": config.cognify_model,
        }
    )
    cognee.config.set_vector_db_config(
        {
            "vector_db_provider": "pgvector",
            "vector_db_url": config.db_url,
        }
    )
