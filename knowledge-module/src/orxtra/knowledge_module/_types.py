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


@dataclass
class DomainConcept:
    """A core concept in the problem domain."""

    name: str
    definition: str
    related_concepts: list[str]
    source_file: str | None = None


@dataclass
class Convention:
    """A coding or process convention."""

    name: str
    rule: str
    rationale: str
    scope: str  # e.g., "project", "module", "file"


@dataclass
class FailurePattern:
    """A pattern that has caused failures."""

    pattern: str
    symptom: str
    fix: str
    occurrences: int = 1


@dataclass
class LearnedFact:
    """A fact learned during execution."""

    fact: str
    confidence: float
    source: str
    tags: list[str]
