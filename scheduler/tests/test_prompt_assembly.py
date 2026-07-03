"""Golden-output tests for _assemble_agent_prompt.

Captures the exact output of the prompt assembly pipeline for
representative scenarios. These serve as the baseline for verifying
equivalence when the implementation switches from inline string
construction to the compose engine.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import UUID

import pytest
import uuid6
from orxtra.notepad import NotepadEntry, format_notepad
from orxtra.protocols import TaskSpec

from .conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)


def _make_scheduler(
    tmp_path: Any,  # noqa: ANN401
    run_id: UUID | None = None,
) -> Any:  # noqa: ANN401
    from orxtra.scheduler._executor import Scheduler

    return Scheduler(
        trace_writer=MockTraceWriter(),  # type: ignore[arg-type]
        transport_registry={
            "anthropic": MockTransport(auto_execute_tools=True),
        },  # type: ignore[dict-item]
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id or uuid6.uuid7(),
        read_root=tmp_path,
        autonomy_level="max",
    )


class TestPromptAssemblyGolden:
    """Golden-output tests for the prompt assembly layers.

    Each test exercises one or more layers and verifies the exact text
    structure produced. When Step 2 replaces the inline construction with
    compose fragments, these tests prove equivalence.
    """

    async def test_base_prompt_only(self, tmp_path: Any) -> None:  # noqa: ANN401
        """Layer 1: base task prompt with task-ID preamble."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="simple",
            agent="test-agent",
            task_prompt="Do the work.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 1, attempt_id, [],
        )

        assert result.startswith(f"Your task ID is {task_id}.")
        assert "Call start_task first." in result
        assert "Do the work." in result
        # No extra sections
        assert "## Active Constraints" not in result
        assert "## Lessons" not in result
        assert "## Prior Failure Context" not in result
        assert "Context from previous steps" not in result

    async def test_with_variables(self, tmp_path: Any) -> None:  # noqa: ANN401
        """Layer 1: variable substitution in task prompt."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="var-task",
            agent="test-agent",
            task_prompt="Process {item} now.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, {"item": "alpha"}, 1, attempt_id, [],
        )

        assert "Process alpha now." in result
        assert "{item}" not in result

    async def test_with_constraints(self, tmp_path: Any) -> None:  # noqa: ANN401
        """Layer 2: active constraints section."""
        sched = _make_scheduler(tmp_path)
        sched._active_constraints.append(  # noqa: SLF001
            ("No new dependencies", "mechanical"),
        )
        sched._active_constraints.append(  # noqa: SLF001
            ("Keep files small", "advisory"),
        )
        task = TaskSpec(
            name="constrained",
            agent="test-agent",
            task_prompt="Do it.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 1, attempt_id, [],
        )

        assert "## Active Constraints" in result
        assert "- No new dependencies (mechanical)" in result
        assert "- Keep files small (advisory)" in result

    async def test_with_notepad_entries(self, tmp_path: Any) -> None:  # noqa: ANN401
        """Layer 2: notepad entries section."""
        run_id = uuid6.uuid7()
        sched = _make_scheduler(tmp_path, run_id=run_id)
        entry = NotepadEntry(
            run_id=run_id,
            task_name="prior-task",
            agent_name="analyst",
            entry_type="learning",
            text="The API uses v2 endpoints",
            created_at=datetime.now(UTC),
        )
        sched._notepad_entries.append(entry)  # noqa: SLF001

        task = TaskSpec(
            name="noted",
            agent="test-agent",
            task_prompt="Do it.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 1, attempt_id, [],
        )

        assert "Context from previous steps" in result
        assert "The API uses v2 endpoints" in result

    async def test_with_lessons(self, tmp_path: Any) -> None:  # noqa: ANN401
        """Layer 2: verified and stale lessons."""
        sched = _make_scheduler(tmp_path)
        sched._lessons.extend([  # noqa: SLF001
            {"text": "Always run tests", "stale": False},
            {"text": "Old pattern", "stale": True},
        ])

        task = TaskSpec(
            name="lessons",
            agent="test-agent",
            task_prompt="Do it.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 1, attempt_id, [],
        )

        assert "## Lessons (verified)" in result
        assert "- Always run tests" in result
        assert "## Lessons (may be stale)" in result
        assert "- Old pattern" in result
        assert "stale: source modified after lesson was created" in result

    async def test_with_prior_failures(self, tmp_path: Any) -> None:  # noqa: ANN401
        """Layer 2: prior failure context on retry."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="retry-task",
            agent="test-agent",
            task_prompt="Do it.",
            retry=3,
            retry_inject_failure=True,
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()
        prior = [
            {"attempt": 1, "error": "Tests failed"},
            {"attempt": 2, "error": "Lint errors"},
        ]

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 3, attempt_id, prior,
        )

        assert "## Prior Failure Context" in result
        assert "Prior attempt 1 failed: Tests failed" in result
        assert "Prior attempt 2 failed: Lint errors" in result

    async def test_no_prior_failures_on_first_attempt(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Prior failures not shown on attempt 1."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="first",
            agent="test-agent",
            task_prompt="Do it.",
            retry=3,
            retry_inject_failure=True,
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 1, attempt_id, [],
        )

        assert "## Prior Failure Context" not in result

    async def test_all_layers_combined(self, tmp_path: Any) -> None:  # noqa: ANN401
        """All layers present in the correct order."""
        run_id = uuid6.uuid7()
        sched = _make_scheduler(tmp_path, run_id=run_id)
        sched._active_constraints.append(  # noqa: SLF001
            ("No new deps", "mechanical"),
        )
        entry = NotepadEntry(
            run_id=run_id,
            task_name="prior",
            agent_name="analyst",
            entry_type="decision",
            text="Use v2 API",
            created_at=datetime.now(UTC),
        )
        sched._notepad_entries.append(entry)  # noqa: SLF001
        sched._lessons.append(  # noqa: SLF001
            {"text": "Run tests first", "stale": False},
        )

        task = TaskSpec(
            name="combined",
            agent="test-agent",
            task_prompt="Build the feature.",
            retry=2,
            retry_inject_failure=True,
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()
        prior = [{"attempt": 1, "error": "Build failed"}]

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, None, 2, attempt_id, prior,
        )

        # Verify layer ordering: preamble < prompt < constraints
        # < notepad < lessons < failures
        preamble_pos = result.index("Your task ID is")
        prompt_pos = result.index("Build the feature.")
        constraints_pos = result.index("## Active Constraints")
        notepad_pos = result.index("Context from previous steps")
        lessons_pos = result.index("## Lessons (verified)")
        failures_pos = result.index("## Prior Failure Context")

        assert preamble_pos < prompt_pos
        assert prompt_pos < constraints_pos
        assert constraints_pos < notepad_pos
        assert notepad_pos < lessons_pos
        assert lessons_pos < failures_pos


class TestNotepadFormatGolden:
    """Golden-output tests for the notepad format_notepad function.

    Captures the exact rendering so the compose template replacement
    produces identical output.
    """

    def test_empty_entries(self) -> None:
        result = format_notepad([])
        assert "## Context from previous steps" in result
        assert "### Learnings" in result
        assert "### Decisions" in result
        assert "### Issues" in result
        assert "- (none)" in result

    def test_grouped_entries(self) -> None:
        run_id = uuid6.uuid7()
        now = datetime.now(UTC)
        entries = [
            NotepadEntry(
                run_id=run_id,
                task_name="t1",
                agent_name="a1",
                entry_type="learning",
                text="Learned something",
                created_at=now,
            ),
            NotepadEntry(
                run_id=run_id,
                task_name="t2",
                agent_name="a2",
                entry_type="decision",
                text="Decided something",
                created_at=now,
            ),
        ]
        result = format_notepad(entries)

        assert "- [t1/a1] Learned something" in result
        assert "- [t2/a2] Decided something" in result
        # Issues group should be empty
        lines = result.split("\n")
        issues_idx = next(
            i for i, line in enumerate(lines)
            if "### Issues" in line
        )
        assert lines[issues_idx + 1] == "- (none)"


class TestResolvePromptLenient:
    """Tests documenting the lenient behavior of _resolve_prompt.

    These establish the baseline: lenient substitution passes through
    unknown placeholders and ignores unused variables. After Step 2,
    equivalent tests should verify that strict substitution raises
    errors instead.
    """

    def test_unknown_placeholder_passes_through(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Lenient: unknown {placeholder} survives in output."""
        from orxtra.scheduler._agent_execution import AgentExecutionMixin

        result = AgentExecutionMixin._resolve_prompt(
            "Hello {unknown} world", {},
        )
        assert result == "Hello {unknown} world"

    def test_unused_variable_ignored(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Lenient: variable with no matching placeholder is silently ignored."""
        from orxtra.scheduler._agent_execution import AgentExecutionMixin

        result = AgentExecutionMixin._resolve_prompt(
            "Hello world", {"extra": "value"},
        )
        assert result == "Hello world"

    def test_partial_match(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Lenient: some variables match, others don't -- both pass."""
        from orxtra.scheduler._agent_execution import AgentExecutionMixin

        result = AgentExecutionMixin._resolve_prompt(
            "Use {matched} and {orphan}",
            {"matched": "found", "unused": "gone"},
        )
        assert result == "Use found and {orphan}"
