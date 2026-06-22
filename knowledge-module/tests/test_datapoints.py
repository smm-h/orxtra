from __future__ import annotations

from orxtra.knowledge_module._types import (
    Convention,
    DomainConcept,
    FailurePattern,
    LearnedFact,
)


class TestDataPointSubclasses:
    def test_domain_concept_creation(self) -> None:
        concept = DomainConcept(
            name="task",
            definition="A unit of work with pre/post-checks",
            related_concepts=["workflow", "run"],
            source_file="scheduler/DESIGN.md",
        )
        assert concept.name == "task"
        assert concept.definition == "A unit of work with pre/post-checks"
        assert concept.related_concepts == ["workflow", "run"]
        assert concept.source_file == "scheduler/DESIGN.md"

    def test_domain_concept_default_source_file(self) -> None:
        concept = DomainConcept(
            name="task",
            definition="A unit of work",
            related_concepts=[],
        )
        assert concept.source_file is None

    def test_convention_creation(self) -> None:
        conv = Convention(
            name="no-bash-tool",
            rule="Never expose a raw bash/shell tool",
            rationale="Granular tools are safer and more auditable",
            scope="project",
        )
        assert conv.name == "no-bash-tool"
        assert conv.rule == "Never expose a raw bash/shell tool"
        assert conv.rationale == "Granular tools are safer and more auditable"
        assert conv.scope == "project"

    def test_failure_pattern_default_occurrences(self) -> None:
        pattern = FailurePattern(
            pattern="Stale write",
            symptom="File content reverted to old version",
            fix="Use atomic replace with version check",
        )
        assert pattern.pattern == "Stale write"
        assert pattern.symptom == "File content reverted to old version"
        assert pattern.fix == "Use atomic replace with version check"
        assert pattern.occurrences == 1

    def test_failure_pattern_custom_occurrences(self) -> None:
        pattern = FailurePattern(
            pattern="Stale write",
            symptom="File content reverted",
            fix="Atomic replace",
            occurrences=5,
        )
        assert pattern.occurrences == 5

    def test_learned_fact_creation(self) -> None:
        fact = LearnedFact(
            fact="asyncpg pools must be closed explicitly",
            confidence=0.95,
            source="runtime observation",
            tags=["asyncpg", "cleanup"],
        )
        assert fact.fact == "asyncpg pools must be closed explicitly"
        assert fact.confidence == 0.95
        assert fact.source == "runtime observation"
        assert fact.tags == ["asyncpg", "cleanup"]
