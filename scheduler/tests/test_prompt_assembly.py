"""Golden-output tests for _assemble_agent_prompt.

Captures the exact output of the prompt assembly pipeline for
representative scenarios. These serve as the baseline for verifying
equivalence when the implementation switches from inline string
construction to the compose engine.

Also includes:
- Red tests proving strict substitution rejects lenient constructs
- Tests verifying prompt templates are the single source of truth
- Tests for the compose-based prompt providers
"""
from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import pytest
import uuid6
from orxtra.compose import resolve_variables
from orxtra.notepad import NotepadEntry
from orxtra.scheduler._prompt_providers import _render_notepad
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
    """Golden-output tests for the _render_notepad function.

    Captures the exact rendering produced by the compose-path
    notepad renderer in _prompt_providers.
    """

    def test_empty_entries(self) -> None:
        result = _render_notepad([])
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
        result = _render_notepad(entries)

        assert "- [t1/a1] Learned something" in result
        assert "- [t2/a2] Decided something" in result
        # Issues group should be empty
        lines = result.split("\n")
        issues_idx = next(
            i for i, line in enumerate(lines)
            if "### Issues" in line
        )
        assert lines[issues_idx + 1] == "- (none)"


class TestSchedulerStrictSubstitution:
    """Integration tests proving the scheduler's prompt assembly
    uses strict variable substitution via resolve_variables.

    Unresolved placeholders in task prompts raise ValueError.
    Unused variables are silently filtered (workflow executor
    accumulates all dependency outputs, tasks use a subset).
    Normal substitution works correctly.
    """

    async def test_unknown_placeholder_raises(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Strict: unknown {placeholder} in task_prompt is a hard error."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="strict-unknown",
            agent="test-agent",
            task_prompt="Hello {unknown} world",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        with pytest.raises(ValueError, match="Unresolved placeholder"):
            await sched._assemble_agent_prompt(  # noqa: SLF001
                task, task_id, {}, 1, attempt_id, [],
            )

    async def test_unknown_placeholder_with_some_vars_raises(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Strict: unresolved placeholder raises even when some match."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="strict-partial",
            agent="test-agent",
            task_prompt="Use {matched} and {orphan}",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        with pytest.raises(ValueError, match="Unresolved placeholder"):
            await sched._assemble_agent_prompt(  # noqa: SLF001
                task, task_id, {"matched": "found"}, 1, attempt_id, [],
            )

    async def test_unused_variables_filtered(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Unused variables are silently filtered (workflow pattern).

        The workflow executor passes all accumulated dependency outputs
        as variables. Tasks use only a subset. Unused variables must
        not raise -- they are filtered before strict resolution.
        """
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="strict-filtered",
            agent="test-agent",
            task_prompt="Hello world",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        # Should NOT raise despite extra unused variables
        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id,
            {"extra": "value", "another": "unused"},
            1, attempt_id, [],
        )

        assert "Hello world" in result

    async def test_normal_substitution_works(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Strict: matched variables are substituted correctly."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="strict-ok",
            agent="test-agent",
            task_prompt="Process {item} in {mode}",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id,
            {"item": "alpha", "mode": "fast"},
            1, attempt_id, [],
        )

        assert "Process alpha in fast" in result
        assert "{item}" not in result
        assert "{mode}" not in result

    async def test_non_string_values_coerced(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Non-string variable values are coerced to str."""
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="strict-coerce",
            agent="test-agent",
            task_prompt="Count is {count}",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id, {"count": 42}, 1, attempt_id, [],
        )

        assert "Count is 42" in result

    async def test_subset_used_from_accumulated_variables(
        self, tmp_path: Any,  # noqa: ANN401
    ) -> None:
        """Only referenced variables are used; extras are filtered.

        Simulates the workflow pattern: task b uses {a_output}
        but also receives a_text and a_result from the executor.
        """
        sched = _make_scheduler(tmp_path)
        task = TaskSpec(
            name="strict-subset",
            agent="test-agent",
            task_prompt="Use {a_output} for processing",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()

        result = await sched._assemble_agent_prompt(  # noqa: SLF001
            task, task_id,
            {
                "a_output": "result-a",
                "a_text": "text-a",
                "a_result": {"passed": True},
            },
            1, attempt_id, [],
        )

        assert "Use result-a for processing" in result
        assert "{a_output}" not in result


class TestStrictSubstitutionRejects:
    """Tests proving strict substitution hard-errors on
    constructs that lenient substitution would have tolerated.

    These test the compose engine's resolve_variables directly,
    complementing the scheduler integration tests above.
    """

    def test_unknown_placeholder_raises(self) -> None:
        """Strict: unknown {placeholder} is a hard error."""
        with pytest.raises(ValueError, match="Unresolved placeholder"):
            resolve_variables("Hello {unknown} world", {})

    def test_unused_variable_raises(self) -> None:
        """Strict: variable with no matching placeholder is a hard error."""
        with pytest.raises(ValueError, match="Unused variable"):
            resolve_variables("Hello world", {"extra": "value"})

    def test_partial_match_unresolved_raises(self) -> None:
        """Strict: unresolved placeholder raises even with other matches."""
        with pytest.raises(ValueError, match="Unresolved placeholder"):
            resolve_variables(
                "Use {matched} and {orphan}",
                {"matched": "found"},
            )

    def test_partial_match_unused_raises(self) -> None:
        """Strict: unused variable raises even with other matches."""
        with pytest.raises(ValueError, match="Unused variable"):
            resolve_variables(
                "Use {matched}",
                {"matched": "found", "unused": "gone"},
            )


class TestPromptTemplatesExist:
    """Verify all prompt templates exist as packaged .md files.

    Retired section-header strings should only appear in .md templates,
    not in Python source code.
    """

    _PROMPTS_DIR = (
        Path(__file__).resolve().parents[1]
        / "src" / "orxtra" / "scheduler" / "prompts"
    )

    _EXPECTED_TEMPLATES = [
        "task_preamble",
        "constraints",
        "notepad",
        "lessons_verified",
        "lessons_stale",
        "prior_failures",
        "handoff_request",
        "handoff_resume",
        "orchestrator_resume",
        "escalation_to_parent",
        "refine_context",
        "decision_point_observation",
        "decision_point_suggestion",
        "notepad_learning",
        "notepad_decision",
        "notepad_issue",
    ]

    def test_all_templates_exist(self) -> None:
        """Every expected template file exists."""
        for name in self._EXPECTED_TEMPLATES:
            path = self._PROMPTS_DIR / f"{name}.md"
            assert path.is_file(), f"Missing template: {path}"

    def test_templates_are_nonempty(self) -> None:
        """Templates have content."""
        for name in self._EXPECTED_TEMPLATES:
            path = self._PROMPTS_DIR / f"{name}.md"
            content = path.read_text()
            assert len(content.strip()) > 0, (
                f"Empty template: {name}"
            )

    def test_section_headers_not_in_python_source(self) -> None:
        """Retired section-header strings appear only in .md templates.

        These strings were previously hard-coded in _agent_execution.py
        and _task_dispatch.py. After the compose migration, they should
        only exist in the prompts/ .md files.
        """
        src_dir = (
            Path(__file__).resolve().parents[1]
            / "src" / "orxtra" / "scheduler"
        )
        retired_strings = [
            '"## Active Constraints"',
            '"## Lessons (verified)"',
            '"## Lessons (may be stale)"',
            '"## Prior Failure Context"',
            '"## Context from previous steps"',
        ]

        py_files = list(src_dir.glob("*.py"))
        for py_file in py_files:
            content = py_file.read_text()
            for retired in retired_strings:
                # Strip quotes for the search
                bare = retired.strip('"')
                assert bare not in content, (
                    f"Retired header {retired!r} found in"
                    f" {py_file.name}"
                )


class TestPromptProviders:
    """Unit tests for the fragment providers."""

    def test_task_preamble_provider(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            TaskPreambleProvider,
        )

        provider = TaskPreambleProvider()
        frags = provider.fragments({"task_id": "abc-123"})
        assert len(frags) == 1
        assert "abc-123" in frags[0].content
        assert "start_task" in frags[0].content
        assert frags[0].priority == 10

    def test_task_preamble_no_task_id(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            TaskPreambleProvider,
        )

        provider = TaskPreambleProvider()
        frags = provider.fragments({})
        assert frags == []

    def test_task_prompt_provider(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            TaskPromptProvider,
        )

        provider = TaskPromptProvider()
        frags = provider.fragments(
            {"task_prompt": "Build the feature"},
        )
        assert len(frags) == 1
        assert frags[0].content == "Build the feature"
        assert frags[0].priority == 20

    def test_constraints_provider(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            ConstraintsProvider,
        )

        provider = ConstraintsProvider()
        frags = provider.fragments({
            "constraints": [
                ("No deps", "mechanical"),
                ("Keep small", "advisory"),
            ],
        })
        assert len(frags) == 1
        assert "## Active Constraints" in frags[0].content
        assert "- No deps (mechanical)" in frags[0].content
        assert "- Keep small (advisory)" in frags[0].content
        assert frags[0].priority == 30

    def test_constraints_provider_empty(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            ConstraintsProvider,
        )

        provider = ConstraintsProvider()
        frags = provider.fragments({"constraints": []})
        assert frags == []

    def test_notepad_provider(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            NotepadProvider,
        )

        run_id = uuid6.uuid7()
        now = datetime.now(UTC)
        entries = [
            NotepadEntry(
                run_id=run_id,
                task_name="t1",
                agent_name="a1",
                entry_type="learning",
                text="Found something",
                created_at=now,
            ),
        ]
        provider = NotepadProvider()
        frags = provider.fragments(
            {"notepad_entries": entries},
        )
        assert len(frags) == 1
        assert "Context from previous steps" in frags[0].content
        assert "Found something" in frags[0].content
        assert frags[0].priority == 40

    def test_notepad_provider_empty(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            NotepadProvider,
        )

        provider = NotepadProvider()
        frags = provider.fragments({"notepad_entries": []})
        assert frags == []

    def test_lessons_provider_fresh_only(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            LessonsProvider,
        )

        provider = LessonsProvider()
        frags = provider.fragments({
            "lessons": [
                {"text": "Always test", "stale": False},
            ],
        })
        assert len(frags) == 1
        assert "## Lessons (verified)" in frags[0].content
        assert "- Always test" in frags[0].content
        assert frags[0].priority == 50

    def test_lessons_provider_stale_only(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            LessonsProvider,
        )

        provider = LessonsProvider()
        frags = provider.fragments({
            "lessons": [
                {"text": "Old way", "stale": True},
            ],
        })
        assert len(frags) == 1
        assert "## Lessons (may be stale)" in frags[0].content
        assert "stale: source modified" in frags[0].content
        assert frags[0].priority == 55

    def test_lessons_provider_both(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            LessonsProvider,
        )

        provider = LessonsProvider()
        frags = provider.fragments({
            "lessons": [
                {"text": "Fresh", "stale": False},
                {"text": "Old", "stale": True},
            ],
        })
        assert len(frags) == 2
        names = {f.name for f in frags}
        assert names == {"lessons_verified", "lessons_stale"}

    def test_failure_context_provider(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            FailureContextProvider,
        )

        provider = FailureContextProvider()
        frags = provider.fragments({
            "attempt": 3,
            "retry_inject_failure": True,
            "prior_attempts": [
                {"attempt": 1, "error": "Boom"},
                {"attempt": 2, "error": "Crash"},
            ],
        })
        assert len(frags) == 1
        assert "## Prior Failure Context" in frags[0].content
        assert "Prior attempt 1 failed: Boom" in frags[0].content
        assert "Prior attempt 2 failed: Crash" in frags[0].content
        assert frags[0].priority == 60

    def test_failure_context_first_attempt(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            FailureContextProvider,
        )

        provider = FailureContextProvider()
        frags = provider.fragments({
            "attempt": 1,
            "retry_inject_failure": True,
            "prior_attempts": [],
        })
        assert frags == []

    def test_failure_context_no_inject(self) -> None:
        from orxtra.scheduler._prompt_providers import (
            FailureContextProvider,
        )

        provider = FailureContextProvider()
        frags = provider.fragments({
            "attempt": 2,
            "retry_inject_failure": False,
            "prior_attempts": [
                {"attempt": 1, "error": "Boom"},
            ],
        })
        assert frags == []


class TestPromptTemplateRendering:
    """Tests for the prompt template loader and strict rendering."""

    def test_render_template_strict(self) -> None:
        from orxtra.scheduler._prompt_templates import (
            render_template,
        )

        result = render_template(
            "orchestrator_resume",
            {
                "child_task_id": "abc-123",
                "child_result": "done",
            },
        )
        assert "abc-123" in result
        assert "done" in result

    def test_render_template_missing_var_raises(self) -> None:
        from orxtra.scheduler._prompt_templates import (
            render_template,
        )

        with pytest.raises(ValueError, match="Unresolved"):
            render_template(
                "orchestrator_resume",
                {"child_task_id": "abc"},
                # missing child_result
            )

    def test_render_template_unused_var_raises(self) -> None:
        from orxtra.scheduler._prompt_templates import (
            render_template,
        )

        with pytest.raises(ValueError, match="Unused"):
            render_template(
                "decision_point_suggestion",
                {"extra": "value"},
            )

    def test_load_template_caches(self) -> None:
        from orxtra.scheduler._prompt_templates import (
            _cache,
            load_template,
        )

        # Clear the cache for this test
        _cache.pop("task_preamble", None)
        first = load_template("task_preamble")
        assert "task_preamble" in _cache
        second = load_template("task_preamble")
        assert first is second  # Same object from cache


class TestNotepadRenderingGolden:
    """Golden tests for _render_notepad standalone output.

    format_notepad has been deleted from the notepad module;
    _render_notepad in _prompt_providers is the single renderer.
    These verify its exact output structure.
    """

    def test_empty_entries_structure(self) -> None:
        result = _render_notepad([])
        assert result.startswith("## Context from previous steps")
        assert "### Learnings\n- (none)" in result
        assert "### Decisions\n- (none)" in result
        assert "### Issues\n- (none)" in result
        assert result.endswith("\n")

    def test_populated_entries_structure(self) -> None:
        run_id = uuid6.uuid7()
        now = datetime.now(UTC)
        entries = [
            NotepadEntry(
                run_id=run_id,
                task_name="task-a",
                agent_name="coder",
                entry_type="learning",
                text="Use v2 API",
                created_at=now,
            ),
            NotepadEntry(
                run_id=run_id,
                task_name="task-b",
                agent_name="reviewer",
                entry_type="decision",
                text="Skip linting",
                created_at=now,
            ),
            NotepadEntry(
                run_id=run_id,
                task_name="task-c",
                agent_name="analyst",
                entry_type="issue",
                text="Missing test coverage",
                created_at=now,
            ),
        ]
        result = _render_notepad(entries)

        assert "- [task-a/coder] Use v2 API" in result
        assert "- [task-b/reviewer] Skip linting" in result
        assert "- [task-c/analyst] Missing test coverage" in result
        # All types populated means no (none) markers
        assert "(none)" not in result
        assert result.endswith("\n")
