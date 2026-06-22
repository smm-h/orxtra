from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.knowledge_module._cognee_import import require_cognee

if TYPE_CHECKING:
    from orxtra.knowledge_module._types import KnowledgeConfig


def configure_cognee(config: KnowledgeConfig) -> None:
    cognee = require_cognee()

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
