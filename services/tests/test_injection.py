"""Integration tests for the injection point wiring (6.4).

Verifies that the services-built refresh callbacks correctly bridge
trace data into the scheduler's in-memory lists:

- Constraints written to trace appear in the assembled prompt
- Lessons read from trace get staleness tagging via overseer
- Notepad entries read from trace appear in the assembled prompt
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import patch

import uuid6
from orxtra.compose import CompositionEngine
from orxtra.scheduler._prompt_providers import (
    ConstraintsProvider,
    LessonsProvider,
    NotepadProvider,
)
from orxtra.services._injection import (
    build_constraints_refresher,
    build_lessons_refresher,
    build_notepad_refresher,
)
from orxtra.trace import InMemoryBackend

# InMemoryBackend does not enforce the principals FK; a shared stand-in id
# suffices for the creating actor in these fixtures.
_CREATED_BY = uuid6.uuid7()


class TestConstraintsRefresher:
    """Constraint refresh: write to trace, read via callback,
    verify prompt content."""

    async def test_constraint_appears_in_prompt(self) -> None:
        """A constraint written to trace appears in the next
        attempt's assembled prompt via the refresh callback."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        # Write a constraint (simulates overseer add_constraint)
        await backend.write_constraint(
            run_id=run_id,
            text="All changes must have tests",
            tier="mechanical",
            kind="overseer",
        )

        # Build the refresh callback
        refresher = build_constraints_refresher(backend)

        # Refresh and verify the data
        constraints = await refresher(run_id)
        assert len(constraints) == 1
        assert constraints[0] == (
            "All changes must have tests",
            "mechanical",
        )

        # Verify it renders in a prompt
        engine = CompositionEngine(
            providers=[ConstraintsProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"constraints": constraints})
        assert "All changes must have tests" in prompt
        assert "mechanical" in prompt
        assert "## Active Constraints" in prompt

    async def test_multiple_constraints(self) -> None:
        """Multiple constraints all appear."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_constraint(
            run_id=run_id,
            text="No new dependencies",
            tier="mechanical",
            kind="overseer",
        )
        await backend.write_constraint(
            run_id=run_id,
            text="Keep files small",
            tier="advisory",
            kind="overseer",
        )

        refresher = build_constraints_refresher(backend)
        constraints = await refresher(run_id)
        assert len(constraints) == 2

        texts = {c[0] for c in constraints}
        assert "No new dependencies" in texts
        assert "Keep files small" in texts

    async def test_empty_constraints(self) -> None:
        """No constraints yields empty list and no prompt section."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        refresher = build_constraints_refresher(backend)
        constraints = await refresher(run_id)
        assert constraints == []

        engine = CompositionEngine(
            providers=[ConstraintsProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"constraints": constraints})
        assert "## Active Constraints" not in prompt

    async def test_constraint_isolation_by_run(self) -> None:
        """Constraints from one run don't leak into another."""
        backend = InMemoryBackend()
        run_a = await backend.create_run("a", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)
        run_b = await backend.create_run("b", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_constraint(
            run_id=run_a,
            text="Run A constraint",
            tier="mechanical",
            kind="overseer",
        )

        refresher = build_constraints_refresher(backend)
        constraints_a = await refresher(run_a)
        constraints_b = await refresher(run_b)

        assert len(constraints_a) == 1
        assert len(constraints_b) == 0


class TestLessonsRefresher:
    """Lesson refresh: write to trace, apply staleness, verify prompt."""

    async def test_fresh_lesson_in_prompt(self) -> None:
        """A lesson whose source file is unchanged appears as verified."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_lesson(
            run_id=run_id,
            text="Always run pytest before commit",
            relevance_tags=["testing"],
            permanent=True,
            source_files=None,
        )

        refresher = build_lessons_refresher(
            backend, Path("/tmp/repo"), ["testing"],
        )
        lessons = await refresher(run_id)
        assert len(lessons) == 1
        assert lessons[0]["text"] == (
            "Always run pytest before commit"
        )
        assert lessons[0]["stale"] is False

        # Verify prompt rendering
        engine = CompositionEngine(
            providers=[LessonsProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"lessons": lessons})
        assert "## Lessons (verified)" in prompt
        assert "Always run pytest before commit" in prompt

    async def test_stale_lesson_labeled(self) -> None:
        """A lesson whose source file changed is labeled stale."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_lesson(
            run_id=run_id,
            text="Old pattern from before refactor",
            relevance_tags=["patterns"],
            permanent=True,
            source_files=["/repo/src/old.py"],
        )

        refresher = build_lessons_refresher(
            backend, Path("/repo"), ["patterns"],
        )

        # Mock filter_stale_lessons to mark the lesson stale
        with patch(
            "orxtra.overseer.filter_stale_lessons",
        ) as mock_filter:
            async def _filter(
                lessons: list[dict[str, Any]],
                repo_dir: Path,
            ) -> tuple[
                list[dict[str, Any]], list[dict[str, Any]],
            ]:
                return [], lessons  # all stale

            mock_filter.side_effect = _filter
            lessons = await refresher(run_id)

        assert len(lessons) == 1
        assert lessons[0]["stale"] is True

        engine = CompositionEngine(
            providers=[LessonsProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"lessons": lessons})
        assert "## Lessons (may be stale)" in prompt
        assert "stale: source modified" in prompt

    async def test_mixed_fresh_and_stale(self) -> None:
        """Fresh and stale lessons appear in their respective sections."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_lesson(
            run_id=run_id,
            text="Fresh lesson",
            relevance_tags=["dev"],
            permanent=True,
        )
        await backend.write_lesson(
            run_id=run_id,
            text="Stale lesson",
            relevance_tags=["dev"],
            permanent=True,
            source_files=["/repo/old.py"],
        )

        refresher = build_lessons_refresher(
            backend, Path("/repo"), ["dev"],
        )

        with patch(
            "orxtra.overseer.filter_stale_lessons",
        ) as mock_filter:
            async def _filter(
                lessons: list[dict[str, Any]],
                repo_dir: Path,
            ) -> tuple[
                list[dict[str, Any]], list[dict[str, Any]],
            ]:
                fresh = [
                    l for l in lessons  # noqa: E741
                    if l.get("source_file") is None
                ]
                stale = [
                    l for l in lessons  # noqa: E741
                    if l.get("source_file") is not None
                ]
                return fresh, stale

            mock_filter.side_effect = _filter
            lessons = await refresher(run_id)

        assert len(lessons) == 2
        fresh = [lesson for lesson in lessons if not lesson["stale"]]
        stale = [lesson for lesson in lessons if lesson["stale"]]
        assert len(fresh) == 1
        assert len(stale) == 1

        engine = CompositionEngine(
            providers=[LessonsProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"lessons": lessons})
        assert "## Lessons (verified)" in prompt
        assert "## Lessons (may be stale)" in prompt

    async def test_no_lessons_empty_prompt(self) -> None:
        """No matching lessons yields no prompt section."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        refresher = build_lessons_refresher(
            backend, Path("/repo"), ["nonexistent-tag"],
        )
        lessons = await refresher(run_id)
        assert lessons == []

    async def test_created_at_normalization(self) -> None:
        """datetime objects are normalized to isoformat strings
        for filter_stale_lessons compatibility."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_lesson(
            run_id=run_id,
            text="Test lesson",
            relevance_tags=["test"],
            permanent=True,
        )

        refresher = build_lessons_refresher(
            backend, Path("/repo"), ["test"],
        )

        with patch(
            "orxtra.overseer.filter_stale_lessons",
        ) as mock_filter:
            async def _filter(
                lessons: list[dict[str, Any]],
                repo_dir: Path,
            ) -> tuple[
                list[dict[str, Any]], list[dict[str, Any]],
            ]:
                # Verify created_at is a string
                for lesson in lessons:
                    assert isinstance(
                        lesson["created_at"], str,
                    ), (
                        "created_at should be normalized"
                        " to isoformat string"
                    )
                return lessons, []

            mock_filter.side_effect = _filter
            await refresher(run_id)


class TestNotepadRefresher:
    """Notepad refresh: write to trace, read via callback,
    verify prompt content."""

    async def test_notepad_appears_in_prompt(self) -> None:
        """Notepad entries written to trace appear in the prompt."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_notepad_entry(
            run_id=run_id,
            task_name="analysis",
            agent_name="analyst",
            entry_type="learning",
            text="The API uses v2 endpoints",
        )

        refresher = build_notepad_refresher(backend)
        entries = await refresher(run_id)
        assert len(entries) == 1
        assert entries[0].text == "The API uses v2 endpoints"
        assert entries[0].entry_type == "learning"

        engine = CompositionEngine(
            providers=[NotepadProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"notepad_entries": entries})
        assert "Context from previous steps" in prompt
        assert "The API uses v2 endpoints" in prompt

    async def test_multiple_notepad_types(self) -> None:
        """Multiple entry types render in their sections."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_notepad_entry(
            run_id=run_id,
            task_name="t1",
            agent_name="a1",
            entry_type="learning",
            text="Learned X",
        )
        await backend.write_notepad_entry(
            run_id=run_id,
            task_name="t2",
            agent_name="a2",
            entry_type="decision",
            text="Decided Y",
        )
        await backend.write_notepad_entry(
            run_id=run_id,
            task_name="t3",
            agent_name="a3",
            entry_type="issue",
            text="Issue Z",
        )

        refresher = build_notepad_refresher(backend)
        entries = await refresher(run_id)
        assert len(entries) == 3

        engine = CompositionEngine(
            providers=[NotepadProvider()],
            separator="\n\n",
        )
        prompt = engine.compose({"notepad_entries": entries})
        assert "Learned X" in prompt
        assert "Decided Y" in prompt
        assert "Issue Z" in prompt

    async def test_empty_notepad(self) -> None:
        """No notepad entries yields no prompt section."""
        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        refresher = build_notepad_refresher(backend)
        entries = await refresher(run_id)
        assert entries == []


class TestSchedulerRefreshIntegration:
    """Integration: verify the scheduler calls refresh callbacks
    and the data appears in the assembled prompt."""

    async def test_constraint_end_to_end(self) -> None:
        """Write constraint to trace, build scheduler with
        refresh callback, verify prompt contains constraint."""
        # Use conftest helpers
        import importlib.util as _ilu

        from orxtra.scheduler._executor import Scheduler

        _spec = _ilu.spec_from_file_location(
            "tests.shared_mocks",
            Path(__file__).resolve().parents[2]
            / "tests" / "shared_mocks.py",
        )
        _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        mock_trace_writer_cls = _mod.MockTraceWriter
        mock_transport_cls = _mod.MockTransport

        from orxtra.agent import Agent
        from orxtra.protocols import TaskSpec

        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        # Write a constraint
        await backend.write_constraint(
            run_id=run_id,
            text="All functions must have docstrings",
            tier="advisory",
            kind="overseer",
        )

        refresher = build_constraints_refresher(backend)

        agent = Agent(
            name="test-agent",
            description="Test",
            prompt="You are a test agent.",
            category="default",
            allow=["read"],
        )

        scheduler = Scheduler(
            run_principal_id=_mod.TEST_RUN_PRINCIPAL_ID,
            trace_writer=mock_trace_writer_cls(),  # type: ignore[arg-type]
            transport_registry={
                "anthropic": mock_transport_cls(
                    auto_execute_tools=True,
                ),
            },  # type: ignore[dict-item]
            agents={"test-agent": agent},
            categories={"default": "anthropic/claude-sonnet-4-6"},
            run_id=run_id,
            read_root=Path("/tmp"),
            autonomy_level="max",
            refresh_constraints=refresher,
        )

        # The scheduler's _active_constraints starts empty
        assert scheduler._active_constraints == []

        # Call the refresh method directly
        await scheduler._refresh_injection_data()

        # Now constraints should be populated
        assert len(scheduler._active_constraints) == 1
        assert scheduler._active_constraints[0] == (
            "All functions must have docstrings",
            "advisory",
        )

        # Verify it appears in assembled prompt
        task = TaskSpec(
            name="test-task",
            agent="test-agent",
            task_prompt="Do work.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()
        prompt = await scheduler._assemble_agent_prompt(
            task, task_id, None, 1, attempt_id, [],
        )
        assert "All functions must have docstrings" in prompt
        assert "advisory" in prompt

    async def test_notepad_end_to_end(self) -> None:
        """Write notepad entry to trace, refresh, verify prompt."""
        import importlib.util as _ilu

        from orxtra.scheduler._executor import Scheduler

        _spec = _ilu.spec_from_file_location(
            "tests.shared_mocks",
            Path(__file__).resolve().parents[2]
            / "tests" / "shared_mocks.py",
        )
        _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        mock_trace_writer_cls = _mod.MockTraceWriter
        mock_transport_cls = _mod.MockTransport

        from orxtra.agent import Agent
        from orxtra.protocols import TaskSpec

        backend = InMemoryBackend()
        run_id = await backend.create_run("test", {}, "max", run_id=uuid6.uuid7(), created_by=_CREATED_BY)

        await backend.write_notepad_entry(
            run_id=run_id,
            task_name="prior-task",
            agent_name="analyst",
            entry_type="learning",
            text="API rate limit is 100 req/min",
        )

        refresher = build_notepad_refresher(backend)

        agent = Agent(
            name="test-agent",
            description="Test",
            prompt="You are a test agent.",
            category="default",
            allow=["read"],
        )

        scheduler = Scheduler(
            run_principal_id=_mod.TEST_RUN_PRINCIPAL_ID,
            trace_writer=mock_trace_writer_cls(),  # type: ignore[arg-type]
            transport_registry={
                "anthropic": mock_transport_cls(
                    auto_execute_tools=True,
                ),
            },  # type: ignore[dict-item]
            agents={"test-agent": agent},
            categories={"default": "anthropic/claude-sonnet-4-6"},
            run_id=run_id,
            read_root=Path("/tmp"),
            autonomy_level="max",
            refresh_notepad=refresher,
        )

        await scheduler._refresh_injection_data()

        assert len(scheduler._notepad_entries) == 1

        task = TaskSpec(
            name="test-task",
            agent="test-agent",
            task_prompt="Do work.",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()
        attempt_id = uuid6.uuid7()
        prompt = await scheduler._assemble_agent_prompt(
            task, task_id, None, 1, attempt_id, [],
        )
        assert "API rate limit is 100 req/min" in prompt
        assert "Context from previous steps" in prompt

    async def test_no_callbacks_leaves_empty(self) -> None:
        """When no refresh callbacks are set, data stays empty."""
        import importlib.util as _ilu

        from orxtra.scheduler._executor import Scheduler

        _spec = _ilu.spec_from_file_location(
            "tests.shared_mocks",
            Path(__file__).resolve().parents[2]
            / "tests" / "shared_mocks.py",
        )
        _mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
        _spec.loader.exec_module(_mod)  # type: ignore[union-attr]
        mock_trace_writer_cls = _mod.MockTraceWriter
        mock_transport_cls = _mod.MockTransport

        from orxtra.agent import Agent

        agent = Agent(
            name="test-agent",
            description="Test",
            prompt="You are a test agent.",
            category="default",
            allow=["read"],
        )

        scheduler = Scheduler(
            run_principal_id=_mod.TEST_RUN_PRINCIPAL_ID,
            trace_writer=mock_trace_writer_cls(),  # type: ignore[arg-type]
            transport_registry={
                "anthropic": mock_transport_cls(
                    auto_execute_tools=True,
                ),
            },  # type: ignore[dict-item]
            agents={"test-agent": agent},
            categories={"default": "anthropic/claude-sonnet-4-6"},
            run_id=uuid6.uuid7(),
            read_root=Path("/tmp"),
            autonomy_level="max",
        )

        await scheduler._refresh_injection_data()

        assert scheduler._active_constraints == []
        assert scheduler._lessons == []
        assert scheduler._notepad_entries == []
