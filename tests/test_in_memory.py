"""Test running a workflow entirely in-memory without PostgreSQL."""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

import pytest
from orxtra.protocols._task import TaskResult, TaskSpec
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._types import WorkflowConfig
from orxtra.trace._memory_backend import InMemoryBackend, InMemoryEventBus


# A simple callable that returns a TaskResult.
# Must be importable, so we define it at module level.
async def _noop_callable(context: Any) -> TaskResult:  # noqa: ANN401
    return TaskResult(
        output="hello from in-memory",
        structured_output={"status": "ok"},
        check_results=[],
    )


CALLABLE_PATH = f"{__name__}:_noop_callable"


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


class TestInMemoryWorkflow:
    """Test that a simple workflow can run entirely in-memory."""

    @pytest.mark.asyncio
    async def test_callable_task_completes(
        self, backend: InMemoryBackend,
    ) -> None:
        """A single callable task runs and completes with InMemoryBackend."""
        # Create the run
        run_id = await backend.create_run("test intent", {}, "max")
        await backend.transition_run(run_id, "running")

        # Build a minimal workflow with a callable task
        task = TaskSpec(
            name="simple_task",
            callable=CALLABLE_PATH,
        )
        workflow = WorkflowConfig(
            name="test-workflow",
            description="In-memory test",
            tasks=[task],
            dependencies={},
        )

        # Create scheduler with the in-memory backend
        scheduler = Scheduler(
            trace_writer=backend,
            transport_registry={},
            agents={},
            categories={},
            run_id=run_id,
            read_root=Path(tempfile.mkdtemp()),
            backend=backend,
            autonomy_level="max",
        )

        # Execute the workflow
        await scheduler.execute_workflow(workflow)

        # Verify: run should still be "running" (start_run completes it)
        # The scheduler itself doesn't transition the run to "completed"
        # -- that's done by start_run(). But tasks should be completed.
        tasks = await backend.list_tasks(run_id)
        assert len(tasks) == 1
        assert tasks[0].name == "simple_task"
        assert tasks[0].status == "completed"

        # Verify attempt was recorded
        assert tasks[0].attempt_count == 1
        attempt = await backend.read_task_attempt(tasks[0].id, 1)
        assert attempt is not None
        assert attempt.status == "completed"
        assert attempt.agent_output == "hello from in-memory"

    @pytest.mark.asyncio
    async def test_full_run_lifecycle(
        self, backend: InMemoryBackend,
    ) -> None:
        """Test the full run lifecycle: create -> running -> completed."""
        run_id = await backend.create_run("full lifecycle", {}, "max")
        await backend.transition_run(run_id, "running")

        task = TaskSpec(
            name="lifecycle_task",
            callable=CALLABLE_PATH,
        )
        workflow = WorkflowConfig(
            name="lifecycle-wf",
            description="Lifecycle test",
            tasks=[task],
            dependencies={},
        )

        scheduler = Scheduler(
            trace_writer=backend,
            transport_registry={},
            agents={},
            categories={},
            run_id=run_id,
            read_root=Path(tempfile.mkdtemp()),
            backend=backend,
            autonomy_level="max",
        )

        await scheduler.execute_workflow(workflow)
        await backend.transition_run(run_id, "completed")

        # Verify the run report
        report = await backend.read_run_report(run_id)
        assert report is not None
        assert report.status == "completed"
        assert report.intent == "full lifecycle"
        assert len(report.tasks) == 1
        assert report.tasks[0].status == "completed"

    @pytest.mark.asyncio
    async def test_multiple_callable_tasks(
        self, backend: InMemoryBackend,
    ) -> None:
        """Multiple sequential callable tasks run in-memory."""
        run_id = await backend.create_run("multi-task", {}, "max")
        await backend.transition_run(run_id, "running")

        tasks = [
            TaskSpec(name="task_a", callable=CALLABLE_PATH),
            TaskSpec(name="task_b", callable=CALLABLE_PATH, depends_on=["task_a"]),
        ]
        workflow = WorkflowConfig(
            name="multi-wf",
            description="Multiple tasks",
            tasks=tasks,
            dependencies={"task_b": ["task_a"]},
        )

        scheduler = Scheduler(
            trace_writer=backend,
            transport_registry={},
            agents={},
            categories={},
            run_id=run_id,
            read_root=Path(tempfile.mkdtemp()),
            backend=backend,
            autonomy_level="max",
        )

        await scheduler.execute_workflow(workflow)

        task_summaries = await backend.list_tasks(run_id)
        assert len(task_summaries) == 2
        assert all(t.status == "completed" for t in task_summaries)

    @pytest.mark.asyncio
    async def test_events_recorded(
        self, backend: InMemoryBackend,
    ) -> None:
        """Verify that task transition events are recorded in-memory."""
        run_id = await backend.create_run("events-test", {}, "max")
        await backend.transition_run(run_id, "running")

        task = TaskSpec(
            name="event_task",
            callable=CALLABLE_PATH,
        )
        workflow = WorkflowConfig(
            name="event-wf",
            description="Event recording test",
            tasks=[task],
            dependencies={},
        )

        scheduler = Scheduler(
            trace_writer=backend,
            transport_registry={},
            agents={},
            categories={},
            run_id=run_id,
            read_root=Path(tempfile.mkdtemp()),
            backend=backend,
            autonomy_level="max",
        )

        await scheduler.execute_workflow(workflow)

        events = await backend.query_events(run_id, event_type="task_transition")
        # Should have at least: created->active, active->postchecking,
        # postchecking->completed
        assert len(events) >= 2

    @pytest.mark.asyncio
    async def test_recovery_runs_on_backend(
        self, backend: InMemoryBackend,
    ) -> None:
        """Crash recovery runs through the backend, not pool."""
        run_id = await backend.create_run("recovery-test", {}, "max")
        await backend.transition_run(run_id, "running")

        task = TaskSpec(
            name="recovery_task",
            callable=CALLABLE_PATH,
        )
        workflow = WorkflowConfig(
            name="recovery-wf",
            description="Recovery test",
            tasks=[task],
            dependencies={},
        )

        scheduler = Scheduler(
            trace_writer=backend,
            transport_registry={},
            agents={},
            categories={},
            run_id=run_id,
            read_root=Path(tempfile.mkdtemp()),
            backend=backend,
            autonomy_level="max",
            # No pool -- recovery must go through backend
        )

        # The workflow should complete without error even with no pool
        await scheduler.execute_workflow(workflow)

        task_summaries = await backend.list_tasks(run_id)
        assert len(task_summaries) == 1
        assert task_summaries[0].status == "completed"
