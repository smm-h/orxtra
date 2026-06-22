from __future__ import annotations

import asyncio
import re
import sys
import types
import uuid
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
import uuid6
from orxtra.protocols._execution import CheckResult
from orxtra.protocols._task import (
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
)
from orxtra.protocols._tool import ToolError
from orxtra.protocols._tools import (
    CreateTaskParams,
    CreateWaitForParams,
    CreateWorkflowParams,
)
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._types import WorkflowConfig
from orxtra.transport import Result, StepFinish, ToolUse

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from orxtra.transport import Event

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)


def _simple_task(
    name: str = "t1",
    agent: str = "test-agent",
    timeout: int = 60,
) -> TaskSpec:
    return TaskSpec(
        name=name,
        agent=agent,
        task_prompt=f"Do {name}",
        timeout=timeout,
        context_refinement=False,
    )


def _simple_workflow(
    tasks: list[TaskSpec] | None = None,
    dependencies: dict[str, list[str]] | None = None,
) -> WorkflowConfig:
    if tasks is None:
        tasks = [_simple_task()]
    return WorkflowConfig(
        name="test-workflow",
        description="Test workflow",
        tasks=tasks,
        dependencies=dependencies or {},
    )


class TestExecuteSimpleWorkflow:
    async def test_single_task_completes(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)

        create_calls = trace_writer.get_calls("create_task")
        assert len(create_calls) == 1
        assert create_calls[0]["name"] == "t1"

    async def test_task_state_reaches_completed(
        self, scheduler: Scheduler,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)

        completed = [
            tid
            for tid, state in scheduler._task_states.items()  # noqa: SLF001
            if state == TaskState.COMPLETED
        ]
        assert len(completed) == 1

    async def test_task_output_stored(
        self, scheduler: Scheduler,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)
        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        assert "t1" in outputs
        assert outputs["t1"] == "Mock response"


class TestExecuteWithDeps:
    async def test_dependency_ordering(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        tasks = [_simple_task("a"), _simple_task("b")]
        config = _simple_workflow(
            tasks=tasks, dependencies={"b": ["a"]},
        )
        await scheduler.execute_workflow(config)

        create_calls = trace_writer.get_calls("create_task")
        assert len(create_calls) == 2

        completed = [
            tid
            for tid, state in scheduler._task_states.items()  # noqa: SLF001
            if state == TaskState.COMPLETED
        ]
        assert len(completed) == 2

    async def test_both_complete(
        self, scheduler: Scheduler,
    ) -> None:
        tasks = [_simple_task("a"), _simple_task("b")]
        config = _simple_workflow(
            tasks=tasks, dependencies={"b": ["a"]},
        )
        await scheduler.execute_workflow(config)

        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        assert "a" in outputs
        assert "b" in outputs


class TestParallelExecution:
    async def test_independent_tasks_both_complete(
        self, scheduler: Scheduler,
    ) -> None:
        tasks = [_simple_task("a"), _simple_task("b")]
        config = _simple_workflow(
            tasks=tasks, dependencies={},
        )
        await scheduler.execute_workflow(config)

        completed = [
            tid
            for tid, state in scheduler._task_states.items()  # noqa: SLF001
            if state == TaskState.COMPLETED
        ]
        assert len(completed) == 2


class TestDiamondDependency:
    async def test_diamond_all_complete(
        self, scheduler: Scheduler,
    ) -> None:
        tasks = [
            _simple_task("a"),
            _simple_task("b"),
            _simple_task("c"),
            _simple_task("d"),
        ]
        deps = {"b": ["a"], "c": ["a"], "d": ["b", "c"]}
        config = _simple_workflow(
            tasks=tasks, dependencies=deps,
        )
        await scheduler.execute_workflow(config)

        completed = [
            tid
            for tid, state in scheduler._task_states.items()  # noqa: SLF001
            if state == TaskState.COMPLETED
        ]
        assert len(completed) == 4


class TestAgentToolCallPath:
    """Agent tasks run through the start_task/end_task
    tool-call path."""

    async def test_start_task_called_by_agent(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)

        transitions = trace_writer.get_calls(
            "transition_task",
        )
        statuses = [
            t["new_status"] for t in transitions
        ]
        assert "prechecking" in statuses
        assert "active" in statuses
        assert "completed" in statuses

    async def test_end_task_stores_output(
        self, scheduler: Scheduler,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)

        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        assert outputs.get("t1") == "Mock response"

    async def test_precheck_failure_blocks(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)

        async def failing_prechecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            return [
                CheckResult(
                    passed=False,
                    message="Precheck failed",
                ),
            ]

        original = Scheduler._run_prechecks  # noqa: SLF001
        Scheduler._run_prechecks = failing_prechecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = _simple_task()
            config = _simple_workflow(tasks=[task])
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": transport},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            states = list(sched._task_states.values())  # noqa: SLF001
            assert TaskState.COMPLETED not in states
            assert (
                TaskState.ESCALATED in states
                or TaskState.PRECHECK_FAILED in states
            )
        finally:
            Scheduler._run_prechecks = original  # type: ignore[assignment]  # noqa: SLF001

    async def test_postcheck_failure_from_end_task(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        call_count = 0

        async def postchecks_fail_then_pass(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [
                    CheckResult(
                        passed=False,
                        message="Check failed",
                    ),
                ]
            return [
                CheckResult(
                    passed=True,
                    message="Check passed",
                ),
            ]

        original = Scheduler._run_postchecks  # noqa: SLF001

        class RetryTransport:
            """Calls start_task and end_task on each
            send, simulating retry."""

            def __init__(self) -> None:
                self._sends = 0

            async def send(
                self,
                message: str,
                **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                self._sends += 1
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}

                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    start_result = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=start_result,
                        status="success",
                    )

                if "end_task" in tool_map:
                    end_result = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=end_result,
                        status="success",
                    )

                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        Scheduler._run_postchecks = postchecks_fail_then_pass  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="retry-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=2,
                retry_resume=False,
                retry_inject_failure=False,
            )
            config = WorkflowConfig(
                name="retry-wf",
                description="Test",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": RetryTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            completed = [
                tid
                for tid, state in sched._task_states.items()  # noqa: SLF001
                if state == TaskState.COMPLETED
            ]
            assert len(completed) == 1
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001


class TestStartTask:
    async def test_transitions_created_to_active(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001

        result = await scheduler.handle_start_task(
            "sess-1", str(task_id),
        )
        assert "active" in result.lower()
        assert scheduler._task_states[task_id] == TaskState.ACTIVE  # noqa: SLF001
        assert scheduler._active_tasks["sess-1"] == task_id  # noqa: SLF001

    async def test_rejects_non_created_task(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.ACTIVE  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001

        with pytest.raises(ToolError):
            await scheduler.handle_start_task(
                "sess-1", str(task_id),
            )

    async def test_rejects_nonexistent_task(
        self, scheduler: Scheduler,
    ) -> None:
        fake_id = uuid6.uuid7()
        with pytest.raises(ToolError):
            await scheduler.handle_start_task(
                "sess-1", str(fake_id),
            )


class TestEndTask:
    async def test_transitions_active_to_completed(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001
        scheduler._task_children[task_id] = []  # noqa: SLF001
        scheduler._task_parents[task_id] = None  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(task_id))
        result = await scheduler.handle_end_task(
            "sess-1", "Done",
        )
        assert "completed" in result.lower()
        assert scheduler._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001

    async def test_stores_output_on_completion(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001
        scheduler._task_children[task_id] = []  # noqa: SLF001
        scheduler._task_parents[task_id] = None  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(task_id))
        await scheduler.handle_end_task(
            "sess-1", "my output",
        )
        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        assert outputs["t1"] == "my output"

    async def test_blocks_with_incomplete_subtasks(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        child_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=parent_id,
            name="child",
            task_type="agent",
        )
        scheduler._task_states[parent_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[parent_id] = parent_task  # noqa: SLF001
        scheduler._task_children[parent_id] = [child_id]  # noqa: SLF001
        scheduler._task_parents[parent_id] = None  # noqa: SLF001
        scheduler._task_states[child_id] = TaskState.ACTIVE  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(parent_id))

        with pytest.raises(ToolError, match="subtask"):
            await scheduler.handle_end_task("sess-1", "Done")

    async def test_auto_commit_runs_before_postchecks(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Auto-commit must run before postchecks so checks verify committed state."""
        call_order: list[str] = []

        original_auto_commit = scheduler._auto_commit  # noqa: SLF001
        original_run_postchecks = scheduler._run_postchecks  # noqa: SLF001

        async def tracking_auto_commit(*a: object, **kw: object) -> None:
            call_order.append("auto_commit")
            return await original_auto_commit(*a, **kw)

        async def tracking_run_postchecks(
            *a: object, **kw: object,
        ) -> list[CheckResult]:
            call_order.append("postchecks")
            return await original_run_postchecks(*a, **kw)

        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001
        scheduler._task_children[task_id] = []  # noqa: SLF001
        scheduler._task_parents[task_id] = None  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(task_id))

        with (
            patch.object(
                scheduler, "_auto_commit",
                side_effect=tracking_auto_commit,
            ),
            patch.object(
                scheduler, "_run_postchecks",
                side_effect=tracking_run_postchecks,
            ),
        ):
            await scheduler.handle_end_task("sess-1", "Done")

        assert call_order.index("auto_commit") < call_order.index("postchecks"), (
            f"auto_commit must run before postchecks, got: {call_order}"
        )


class TestActiveTaskEnforcement:
    async def test_no_active_task_raises(
        self, scheduler: Scheduler,
    ) -> None:
        with pytest.raises(ToolError, match="No active task"):
            scheduler.check_active_task("nonexistent-session")

    async def test_start_task_exempt_from_check(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001

        result = await scheduler.handle_start_task(
            "sess-1", str(task_id),
        )
        assert "active" in result.lower()


class TestHandleCreateTask:
    async def test_creates_child_task(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._task_states[parent_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[parent_id] = parent_task  # noqa: SLF001
        scheduler._task_children[parent_id] = []  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(parent_id))

        params = CreateTaskParams(
            name="child-task",
            agent="test-agent",
            task_prompt="Do child work",
            timeout=30,
            context_refinement=False,
        )
        result = await scheduler.handle_create_task(
            "sess-1", params.model_dump(),
        )
        child_id = uuid.UUID(result)
        assert child_id in scheduler._task_states  # noqa: SLF001
        assert (
            scheduler._task_states[child_id]  # noqa: SLF001
            == TaskState.CREATED
        )
        assert child_id in scheduler._task_children[parent_id]  # noqa: SLF001


class TestHandleCreateWorkflow:
    async def test_creates_workflow_task(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._task_states[parent_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[parent_id] = parent_task  # noqa: SLF001
        scheduler._task_children[parent_id] = []  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(parent_id))

        params = CreateWorkflowParams(
            name="sub-workflow",
            description="A sub workflow",
            goals=["goal1"],
        )
        result = await scheduler.handle_create_workflow(
            "sess-1", params.model_dump(),
        )
        wf_id = uuid.UUID(result)
        assert wf_id in scheduler._task_states  # noqa: SLF001
        assert wf_id in scheduler._task_children[parent_id]  # noqa: SLF001

    async def test_stores_task_spec(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._task_states[parent_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[parent_id] = parent_task  # noqa: SLF001
        scheduler._task_children[parent_id] = []  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(parent_id))

        params = CreateWorkflowParams(
            name="sub-workflow",
            description="A sub workflow",
            goals=["goal1"],
        )
        result = await scheduler.handle_create_workflow(
            "sess-1", params.model_dump(),
        )
        wf_id = uuid.UUID(result)
        assert wf_id in scheduler._task_specs  # noqa: SLF001


class TestHandleCreateWaitFor:
    async def test_creates_wait_for_task(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._task_states[parent_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[parent_id] = parent_task  # noqa: SLF001
        scheduler._task_children[parent_id] = []  # noqa: SLF001

        await scheduler.handle_start_task("sess-1", str(parent_id))

        params = CreateWaitForParams(
            name="wait-task",
            event_name="deploy_done",
            timeout=60,
        )
        result = await scheduler.handle_create_wait_for(
            "sess-1", params.model_dump(),
        )
        wait_id = uuid.UUID(result)
        assert wait_id in scheduler._task_states  # noqa: SLF001
        assert (
            scheduler._task_states[wait_id]  # noqa: SLF001
            == TaskState.CREATED
        )


class TestRetry:
    async def test_postcheck_failure_retries(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        call_count = 0
        original_run_postchecks = Scheduler._run_postchecks  # noqa: SLF001

        async def mock_postchecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [
                    CheckResult(
                        passed=False, message="Check failed",
                    ),
                ]
            return [
                CheckResult(
                    passed=True, message="Check passed",
                ),
            ]

        class RetryTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        Scheduler._run_postchecks = mock_postchecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="retry-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=2,
                retry_resume=False,
                retry_inject_failure=False,
            )
            config = WorkflowConfig(
                name="retry-wf",
                description="Test retry",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": RetryTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            completed = [
                tid
                for tid, state in sched._task_states.items()  # noqa: SLF001
                if state == TaskState.COMPLETED
            ]
            assert len(completed) == 1
        finally:
            Scheduler._run_postchecks = original_run_postchecks  # type: ignore[assignment]  # noqa: SLF001

    async def test_retry_exhausted_escalates(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        async def always_fail_postchecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            return [
                CheckResult(
                    passed=False, message="Always fails",
                ),
            ]

        class RetryTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = always_fail_postchecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="fail-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=1,
                retry_resume=False,
                retry_inject_failure=False,
            )
            config = WorkflowConfig(
                name="fail-wf",
                description="Test escalation",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": RetryTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            escalated = [
                tid
                for tid, state in sched._task_states.items()  # noqa: SLF001
                if state == TaskState.ESCALATED
            ]
            assert len(escalated) == 1
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001

    async def test_escalation_payload_constructed(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        async def always_fail_postchecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            return [
                CheckResult(
                    passed=False, message="Always fails",
                ),
            ]

        class RetryTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = always_fail_postchecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="esc-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=1,
                retry_resume=False,
                retry_inject_failure=False,
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": RetryTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            result = await sched.execute_task(
                task, None,
            )
            assert result.structured_output is not None
            assert "escalation" in result.structured_output
            esc = result.structured_output["escalation"]
            assert esc["task_name"] == "esc-task"
            assert esc["attempts"] == 2
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001


class TestTaskTimeout:
    async def test_timeout_cancels_task(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        class SlowTransport:
            async def send(  # type: ignore[override]
                self, message: str, **kwargs: object,
            ) -> AsyncIterator[Event]:
                await asyncio.sleep(10)
                yield Result(  # pragma: no cover
                    text="late",
                    session_id="s",
                    total_input_tokens=0,
                    total_output_tokens=0,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        task = TaskSpec(
            name="slow-task",
            agent="test-agent",
            task_prompt="Be slow",
            timeout=1,
            context_refinement=False,
        )
        config = WorkflowConfig(
            name="timeout-wf",
            description="Timeout test",
            tasks=[task],
            dependencies={},
        )
        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": SlowTransport()},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
        )
        await sched.execute_workflow(config)

        cancelled = [
            tid
            for tid, state in sched._task_states.items()  # noqa: SLF001
            if state == TaskState.CANCELLED
        ]
        assert len(cancelled) == 1


class TestBudgetTracking:
    async def test_cost_accumulates(
        self, scheduler: Scheduler,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)

        for tid in scheduler._task_costs:  # noqa: SLF001
            assert isinstance(
                scheduler._task_costs[tid],  # noqa: SLF001
                Decimal,
            )


class TestBudgetPersistence:
    """Verify that cost and duration flow through to trace records."""

    async def test_complete_attempt_receives_nonzero_cost(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)

        complete_calls = trace_writer.get_calls(
            "complete_task_attempt",
        )
        assert len(complete_calls) >= 1
        # The mock transport yields 10 input + 5 output tokens
        # using "anthropic/claude-sonnet-4-6" which is in the
        # PRICING_TABLE, so cost should be non-zero.
        # duration_seconds should be > 0 since we use
        # wall-clock timing.
        for call in complete_calls:
            assert isinstance(call["cost_usd"], Decimal)
            assert isinstance(call["duration_seconds"], float)
            assert call["duration_seconds"] > 0.0


class TestAccumulateCostError:
    async def test_unknown_model_raises_immediately(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        class FailTransport:
            """Transport that never gets called because
            the error happens during cost accumulation."""

            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        task = TaskSpec(
            name="bad-model-task",
            agent="test-agent",
            task_prompt="Do work",
            timeout=60,
            context_refinement=False,
        )
        config = WorkflowConfig(
            name="bad-model-wf",
            description="Unknown model test",
            tasks=[task],
            dependencies={},
        )
        # Use a category that maps to a model NOT in
        # the pricing table.
        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"mock-provider": FailTransport()},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories={"default": "mock-provider/nonexistent-model"},
            run_id=run_id,
            read_root=tmp_path,
        )
        with pytest.raises(
            ValueError, match="Unknown model",
        ):
            await sched.execute_workflow(config)


class TestFunctionTask:
    async def test_callable_task_executes(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        task = TaskSpec(
            name="func-task",
            callable="tests.conftest_helpers:sample_callable",
        )
        config = WorkflowConfig(
            name="func-wf",
            description="Function task test",
            tasks=[task],
            dependencies={},
        )

        helpers = types.ModuleType("tests.conftest_helpers")

        async def sample_callable(
            ctx: TaskContext,
        ) -> TaskResult:
            return TaskResult(
                output="function result",
                structured_output={"key": "value"},
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        helpers.sample_callable = sample_callable  # type: ignore[attr-defined]
        sys.modules["tests.conftest_helpers"] = helpers
        sys.modules["tests"] = types.ModuleType("tests")

        try:
            await scheduler.execute_workflow(config)
            outputs = scheduler._task_outputs.get(  # noqa: SLF001
                None, {},
            )
            assert outputs["func-task"] == "function result"
        finally:
            sys.modules.pop("tests.conftest_helpers", None)


class TestForEach:
    async def test_iterates_over_items(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)

        task = TaskSpec(
            name="iter-task",
            agent="test-agent",
            task_prompt="Process {item}",
            timeout=60,
            context_refinement=False,
            for_each="items",
            for_each_abort_on_failure=False,
            max_concurrency=2,
        )

        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": transport},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
        )
        result = await sched.execute_task(
            task, None, variables={"items": ["a", "b", "c"]},
        )
        assert result.structured_output is not None
        assert len(result.structured_output["iterations"]) == 3

    async def test_abort_on_failure(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        call_count = 0

        class FailingTransport:
            async def send(
                self, message: str, **kwargs: object,
            ) -> AsyncIterator[Event]:
                nonlocal call_count
                call_count += 1
                tools = kwargs.get("tools", [])
                tool_map = {
                    t.name: t
                    for t in tools  # type: ignore[union-attr]
                }

                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if call_count == 1:
                    if "start_task" in tool_map:
                        r = await tool_map[
                            "start_task"
                        ].execute({"task_id": task_id_str})
                        yield ToolUse(
                            tool_name="start_task",
                            input={"task_id": task_id_str},
                            output=r,
                            status="success",
                        )
                    if "end_task" in tool_map:
                        r = await tool_map[
                            "end_task"
                        ].execute({"message": "ok"})
                        yield ToolUse(
                            tool_name="end_task",
                            input={"message": "ok"},
                            output=r,
                            status="success",
                        )
                    yield Result(
                        text="ok",
                        session_id=str(uuid6.uuid7()),
                        total_input_tokens=0,
                        total_output_tokens=0,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=2,
                    )
                else:
                    msg = "Simulated failure"
                    raise RuntimeError(msg)

        task = TaskSpec(
            name="abort-task",
            agent="test-agent",
            task_prompt="Process {item}",
            timeout=60,
            context_refinement=False,
            for_each="items",
            for_each_abort_on_failure=True,
            max_concurrency=1,
        )

        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": FailingTransport()},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
        )
        result = await sched.execute_task(
            task,
            None,
            variables={"items": ["a", "b", "c"]},
        )
        assert not all(
            cr.passed for cr in result.check_results
        )

    async def test_abort_transitions_to_failed_state(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        call_count = 0

        class FailSecondTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                nonlocal call_count
                call_count += 1
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}

                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if call_count <= 1:
                    if "start_task" in tool_map:
                        r = await tool_map[
                            "start_task"
                        ].execute({"task_id": task_id_str})
                        yield ToolUse(
                            tool_name="start_task",
                            input={"task_id": task_id_str},
                            output=r,
                            status="success",
                        )
                    if "end_task" in tool_map:
                        r = await tool_map[
                            "end_task"
                        ].execute({"message": "ok"})
                        yield ToolUse(
                            tool_name="end_task",
                            input={"message": "ok"},
                            output=r,
                            status="success",
                        )
                    yield Result(
                        text="ok",
                        session_id=str(uuid6.uuid7()),
                        total_input_tokens=0,
                        total_output_tokens=0,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=2,
                    )
                else:
                    msg = "Simulated failure"
                    raise RuntimeError(msg)

        task = TaskSpec(
            name="abort-task",
            agent="test-agent",
            task_prompt="Process {item}",
            timeout=60,
            context_refinement=False,
            for_each="items",
            for_each_abort_on_failure=True,
            max_concurrency=1,
        )
        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": FailSecondTransport()},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
        )
        await sched.execute_task(
            task,
            None,
            variables={"items": ["a", "b", "c"]},
        )
        for_each_states = [
            s for tid, s in sched._task_states.items()  # noqa: SLF001
            if sched._task_specs.get(tid, TaskSpec(name="")).name == "abort-task"  # noqa: SLF001
        ]
        assert TaskState.POSTCHECK_FAILED in for_each_states

    async def test_max_concurrency_respected(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        concurrent_count = 0
        max_concurrent = 0

        class TrackedTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                nonlocal concurrent_count, max_concurrent
                concurrent_count += 1
                max_concurrent = max(
                    max_concurrent, concurrent_count,
                )
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                await asyncio.sleep(0.05)
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "ok"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "ok"},
                        output=r,
                        status="success",
                    )
                concurrent_count -= 1
                yield Result(
                    text="ok",
                    session_id=str(uuid6.uuid7()),
                    total_input_tokens=0,
                    total_output_tokens=0,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        task = TaskSpec(
            name="conc-task",
            agent="test-agent",
            task_prompt="Process {item}",
            timeout=60,
            context_refinement=False,
            for_each="items",
            for_each_abort_on_failure=False,
            max_concurrency=2,
        )

        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": TrackedTransport()},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
        )
        await sched.execute_task(
            task,
            None,
            variables={"items": list(range(6))},
        )
        assert max_concurrent <= 2


class TestTaskOutputPropagation:
    async def test_output_available_after_completion(
        self, scheduler: Scheduler,
    ) -> None:
        config = _simple_workflow()
        await scheduler.execute_workflow(config)
        outputs = scheduler._task_outputs.get(None, {})  # noqa: SLF001
        assert outputs["t1"] == "Mock response"
        meta = scheduler._task_results_meta.get(  # noqa: SLF001
            None, {},
        )
        assert meta["t1"]["passed"] is True

    async def test_dependent_task_receives_output(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        received_prompts: list[str] = []

        class CapturingTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                received_prompts.append(message)
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "result-a"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "result-a"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="result-a",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        task_a = TaskSpec(
            name="a",
            agent="test-agent",
            task_prompt="Do a",
            timeout=60,
            context_refinement=False,
        )
        task_b = TaskSpec(
            name="b",
            agent="test-agent",
            task_prompt="Use {a_output} and {a_text}",
            timeout=60,
            context_refinement=False,
        )
        config = WorkflowConfig(
            name="dep-wf",
            description="Dep test",
            tasks=[task_a, task_b],
            dependencies={"b": ["a"]},
        )
        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": CapturingTransport()},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
        )
        await sched.execute_workflow(config)

        b_prompt = received_prompts[1]
        assert "result-a" in b_prompt


class TestAbort:
    async def test_abort_cancels_active_tasks(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.ACTIVE  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001

        await scheduler.abort()
        assert scheduler._task_states[task_id] == TaskState.CANCELLED  # noqa: SLF001


class TestSessionTracking:
    async def test_session_mapped_to_task(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = task  # noqa: SLF001

        await scheduler.handle_start_task("sess-a", str(task_id))
        assert scheduler._active_tasks["sess-a"] == task_id  # noqa: SLF001

    async def test_multiple_sessions(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        task_a = _simple_task("ta")
        task_b = _simple_task("tb")
        id_a = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="ta",
            task_type="agent",
        )
        id_b = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="tb",
            task_type="agent",
        )
        scheduler._task_states[id_a] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[id_a] = task_a  # noqa: SLF001
        scheduler._task_states[id_b] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[id_b] = task_b  # noqa: SLF001

        await scheduler.handle_start_task("sess-a", str(id_a))
        await scheduler.handle_start_task("sess-b", str(id_b))
        assert scheduler._active_tasks["sess-a"] == id_a  # noqa: SLF001
        assert scheduler._active_tasks["sess-b"] == id_b  # noqa: SLF001


class TestWorkflowValidation:
    async def test_invalid_workflow_raises(
        self, scheduler: Scheduler,
    ) -> None:
        task = TaskSpec(name="bad")
        config = WorkflowConfig(
            name="bad-wf",
            description="Invalid",
            tasks=[task],
            dependencies={},
        )
        with pytest.raises(ValueError, match="validation failed"):
            await scheduler.execute_workflow(config)


class TestOnSuccessCallback:
    async def test_on_success_invoked(
        self,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        callback_called = False

        helpers = types.ModuleType(
            "tests.callback_helpers",
        )

        async def on_success_fn(ctx: TaskContext) -> None:
            nonlocal callback_called
            callback_called = True

        helpers.on_success_fn = on_success_fn  # type: ignore[attr-defined]
        sys.modules["tests.callback_helpers"] = helpers
        sys.modules["tests"] = types.ModuleType("tests")

        try:
            task = TaskSpec(
                name="cb-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                on_success=(
                    "tests.callback_helpers:on_success_fn"
                ),
            )
            config = WorkflowConfig(
                name="cb-wf",
                description="Callback test",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={
                    "anthropic": MockTransport(auto_execute_tools=True),
                },  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)
            assert callback_called
        finally:
            sys.modules.pop("tests.callback_helpers", None)


class TestPreRetryCallback:
    async def test_pre_retry_invoked(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        pre_retry_called = False

        helpers = types.ModuleType(
            "tests.preretry_helpers",
        )

        async def pre_retry_fn(ctx: TaskContext) -> None:
            nonlocal pre_retry_called
            pre_retry_called = True

        helpers.pre_retry_fn = pre_retry_fn  # type: ignore[attr-defined]
        sys.modules["tests.preretry_helpers"] = helpers
        sys.modules["tests"] = types.ModuleType("tests")

        call_count = 0

        async def fail_then_pass_postchecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [
                    CheckResult(
                        passed=False,
                        message="Failed",
                    ),
                ]
            return [
                CheckResult(
                    passed=True, message="Passed",
                ),
            ]

        class RetryTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = fail_then_pass_postchecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="pr-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=2,
                retry_resume=False,
                retry_inject_failure=False,
                pre_retry=(
                    "tests.preretry_helpers:pre_retry_fn"
                ),
            )
            config = WorkflowConfig(
                name="pr-wf",
                description="Pre-retry test",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": RetryTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)
            assert pre_retry_called
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001
            sys.modules.pop(
                "tests.preretry_helpers", None,
            )

    async def test_pre_retry_abort_escalates(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()

        helpers = types.ModuleType(
            "tests.preretry_abort_helpers",
        )

        async def pre_retry_abort(
            ctx: TaskContext,
        ) -> None:
            msg = "abort retry"
            raise RuntimeError(msg)

        helpers.pre_retry_abort = pre_retry_abort  # type: ignore[attr-defined]
        sys.modules[
            "tests.preretry_abort_helpers"
        ] = helpers
        sys.modules["tests"] = types.ModuleType("tests")

        async def always_fail_postchecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            return [
                CheckResult(
                    passed=False,
                    message="Always fails",
                ),
            ]

        class RetryTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = always_fail_postchecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="abort-pr-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=3,
                retry_resume=False,
                retry_inject_failure=False,
                pre_retry=(
                    "tests.preretry_abort_helpers"
                    ":pre_retry_abort"
                ),
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": RetryTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_task(
                task, None,
            )
            escalated = [
                tid
                for tid, s in sched._task_states.items()  # noqa: SLF001
                if s == TaskState.ESCALATED
            ]
            assert len(escalated) == 1
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001
            sys.modules.pop(
                "tests.preretry_abort_helpers", None,
            )


class TestRetryResume:
    async def test_retry_resume_uses_same_session(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        session_ids_seen: list[str | None] = []
        call_count = 0

        async def fail_then_pass_postchecks(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [
                    CheckResult(
                        passed=False,
                        message="Failed",
                    ),
                ]
            return [
                CheckResult(
                    passed=True, message="Passed",
                ),
            ]

        class TrackingTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                sid = kwargs.get("session_id")
                session_ids_seen.append(sid)
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                final_sid = str(uuid6.uuid7())
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=final_sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = fail_then_pass_postchecks  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="resume-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=2,
                retry_resume=True,
                retry_inject_failure=False,
            )
            config = WorkflowConfig(
                name="resume-wf",
                description="Resume test",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": TrackingTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            assert len(session_ids_seen) == 2
            # First attempt has no prior session
            assert session_ids_seen[0] is None
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001


class TestRetryInjectFailure:
    async def test_injects_failure_context(
        self, run_id: uuid.UUID, tmp_path: Path,
    ) -> None:
        trace_writer = MockTraceWriter()
        received_prompts: list[str] = []
        call_count = 0

        async def fail_then_pass(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [
                    CheckResult(
                        passed=False,
                        message="Failed",
                    ),
                ]
            return [
                CheckResult(
                    passed=True, message="Passed",
                ),
            ]

        class CapturingTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                received_prompts.append(message)
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = fail_then_pass  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="inject-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=2,
                retry_resume=False,
                retry_inject_failure=True,
            )
            config = WorkflowConfig(
                name="inject-wf",
                description="Inject failure test",
                tasks=[task],
                dependencies={},
            )
            sched = Scheduler(
                trace_writer=trace_writer,  # type: ignore[arg-type]
                transport_registry={"anthropic": CapturingTransport()},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            assert len(received_prompts) >= 2
            assert "Prior attempt" in received_prompts[1]
            assert "failed" in received_prompts[1].lower()
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001
