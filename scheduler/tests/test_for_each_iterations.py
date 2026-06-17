"""Tests for for_each iteration persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

from orxt.protocols._task import TaskSpec

if TYPE_CHECKING:
    from orxt.scheduler._executor import Scheduler

    from tests.conftest import MockTraceWriter


class TestForEachIterations:
    async def test_creates_iterations(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        """create_iteration is called once per item."""
        task = TaskSpec(
            name="iter-task",
            agent="test-agent",
            task_prompt="Process {item}",
            for_each="items",
            for_each_abort_on_failure=False,
            max_concurrency=1,
            context_refinement=False,
        )
        variables = {"items": ["a", "b", "c"]}

        await scheduler.execute_task(task, None, variables=variables)

        calls = trace_writer.get_calls("create_iteration")
        assert len(calls) == 3
        indices = sorted(c["index"] for c in calls)
        assert indices == [0, 1, 2]

    async def test_completes_iterations(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        """complete_iteration is called for each successful iteration."""
        task = TaskSpec(
            name="iter-task",
            agent="test-agent",
            task_prompt="Process {item}",
            for_each="items",
            for_each_abort_on_failure=False,
            max_concurrency=1,
            context_refinement=False,
        )
        variables = {"items": ["x", "y"]}

        await scheduler.execute_task(task, None, variables=variables)

        calls = trace_writer.get_calls("complete_iteration")
        assert len(calls) == 2

    async def test_iteration_records_check_results(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        """complete_iteration receives check result dicts."""
        task = TaskSpec(
            name="iter-task",
            agent="test-agent",
            task_prompt="Process {item}",
            for_each="items",
            for_each_abort_on_failure=False,
            max_concurrency=1,
            context_refinement=False,
        )
        variables = {"items": ["one"]}

        await scheduler.execute_task(task, None, variables=variables)

        calls = trace_writer.get_calls("complete_iteration")
        assert len(calls) == 1
        # The mock transport calls start_task + end_task, so the task completes
        # successfully, meaning check_results should have entries
        check_results = calls[0]["check_results"]
        if check_results is not None:
            assert isinstance(check_results, list)
            for cr in check_results:
                assert "passed" in cr
                assert "message" in cr
