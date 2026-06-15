from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict


class KnowledgeConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    db_url: str
    cognify_model: str
    cognify_api_key: str
    max_retrieval_results: int


@dataclass(frozen=True)
class KnowledgeResult:
    text: str
    source: str
    permanent: bool
    relevance_score: float
