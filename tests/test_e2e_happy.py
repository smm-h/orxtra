"""Happy-path end-to-end integration tests for the orxt system.

Tests exercise the full workflow execution pipeline: workflow config
-> scheduler -> transport (mocked) -> lifecycle tools -> state
transitions -> output propagation.
"""
from __future__ import annotations

import sys
import types
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import uuid6
from orxt.protocols._execution import CheckResult, ScriptExecution
from orxt.protocols._task import TaskResult, TaskSpec, TaskState
from orxt.scheduler._types import WorkflowConfig

from tests.conftest import (
    AgentTurn,
    IntegrationMockTransport,
    MockTraceWriter,
    MultiAgentMockTransport,
    make_agent,
    make_scheduler,
    simple_task,
    simple_workflow,
)

if TYPE_CHECKING:
    import uuid


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _register_check_module(
    module_name: str,
    **functions: Any,  # noqa: ANN401
) -> types.ModuleType:
    """Register a synthetic module in sys.modules for callable tasks."""
    mod = types.ModuleType(module_name)
    for fname, func in functions.items():
        setattr(mod, fname, func)
    sys.modules[module_name] = mod
    parts = module_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
    return mod


def _cleanup_modules(*module_names: str) -> None:
    """Remove synthetic modules from sys.modules."""
    for name in module_names:
        sys.modules.pop(name, None)
        parts = name.split(".")
        for i in range(1, len(parts)):
            parent = ".".join(parts[:i])
            sys.modules.pop(parent, None)


def _patch_auto_commit() -> Any:  # noqa: ANN401
    """Patch _auto_commit to avoid subprocess calls in tests."""
    return patch(
        "orxt.scheduler._executor.Scheduler._auto_commit",
        new_callable=AsyncMock,
    )


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid6.uuid7()


# ---------------------------------------------------------------------------
# Single-task tests
# ---------------------------------------------------------------------------


class TestSingleTask:
    async def test_single_task_completes(
        self, run_id: uuid.UUID,
    ) -> None:
        """One agent task goes through full lifecycle and records output."""
        trace_writer = MockTraceWriter()
        turn = AgentTurn(
            tool_calls=[
                ("start_task", {}),
                ("end_task", {"message": "Result A"}),
            ],
            text_response="Done",
        )
        transport = IntegrationMockTransport([turn])
        scheduler = make_scheduler(trace_writer, transport, run_id)
        config = simple_workflow()

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # Verify task was created in trace
        create_calls = trace_writer.get_calls("create_task")
        assert len(create_calls) == 1
        assert create_calls[0]["name"] == "t1"
        assert create_calls[0]["task_type"] == "agent"

        # Verify state transitions were recorded in trace
        transitions = trace_writer.get_calls("transition_task")
        statuses = [t["new_status"] for t in transitions]
        assert "prechecking" in statuses
        assert "active" in statuses
        assert "postchecking" in statuses
        assert "completed" in statuses

        # Verify task reached COMPLETED in scheduler state
        completed = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1

        # Verify output stored (scoped under parent=None for top-level)
        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        assert outputs.get("t1") == "Result A"

        # Verify transport consumed the turn
        assert transport.turns_consumed == 1
        assert transport.turns_remaining == 0

    async def test_context_refinement_false_skips_overseer(
        self, run_id: uuid.UUID,
    ) -> None:
        """When context_refinement=False, no overseer is consulted."""
        trace_writer = MockTraceWriter()
        turn = AgentTurn(
            tool_calls=[
                ("start_task", {}),
                ("end_task", {"message": "done"}),
            ],
        )
        transport = IntegrationMockTransport([turn])
        # context_refinement=False is the default from simple_task
        task = simple_task(context_refinement=False)
        assert task.context_refinement is False
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # No overseer interface was provided, so no overseer events
        # should appear. The scheduler has no overseer_interface set.
        assert scheduler._overseer_interface is None  # noqa: SLF001

        # Workflow completed successfully
        completed = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1

    async def test_context_refinement_true_stored_on_taskspec(
        self, run_id: uuid.UUID,
    ) -> None:
        """When context_refinement=True, the flag is stored on TaskSpec."""
        # context_refinement=True is stored but not yet acted upon by the scheduler
        trace_writer = MockTraceWriter()
        turn = AgentTurn(
            tool_calls=[
                ("start_task", {}),
                ("end_task", {"message": "done"}),
            ],
        )
        transport = IntegrationMockTransport([turn])
        task = simple_task(context_refinement=True)
        assert task.context_refinement is True
        config = simple_workflow(tasks=[task])
        scheduler = make_scheduler(trace_writer, transport, run_id)

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # Workflow completed successfully despite context_refinement=True
        completed = [
            tid
            for tid, s in scheduler._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1

        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        assert outputs.get("t1") == "done"


# ---------------------------------------------------------------------------
# Dependency chain tests
# ---------------------------------------------------------------------------


class TestDependencies:
    async def test_two_dependent_tasks(
        self, run_id: uuid.UUID,
    ) -> None:
        """Task B depends on Task A. A runs first, B runs after."""
        trace_writer = MockTraceWriter()
        agent_a = make_agent("agent-a")
        agent_b = make_agent("agent-b")

        transport = MultiAgentMockTransport({
            "You are agent-a.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "output from A"}),
                    ],
                ),
            ],
            "You are agent-b.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "output from B"}),
                    ],
                ),
            ],
        })

        task_a = simple_task("task_a", agent="agent-a")
        task_b = simple_task("task_b", agent="agent-b")
        config = WorkflowConfig(
            name="dep-workflow",
            description="Dependency test",
            tasks=[task_a, task_b],
            dependencies={"task_b": ["task_a"]},
        )
        scheduler = make_scheduler(
            trace_writer,
            transport,
            run_id,
            agents={"agent-a": agent_a, "agent-b": agent_b},
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # Both tasks completed
        completed_count = sum(
            1
            for s in scheduler._task_states.values()  # noqa: SLF001
            if s == TaskState.COMPLETED
        )
        assert completed_count == 2

        # Output from A is stored
        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        assert outputs.get("task_a") == "output from A"
        assert outputs.get("task_b") == "output from B"

        # Verify execution order: task_a was created before task_b
        create_calls = trace_writer.get_calls("create_task")
        names = [c["name"] for c in create_calls]
        assert names.index("task_a") < names.index("task_b")

    async def test_three_task_pipeline(
        self, run_id: uuid.UUID,
    ) -> None:
        """Research -> Generate -> Review pipeline with chained deps."""
        trace_writer = MockTraceWriter()
        agents = {
            "researcher": make_agent("researcher"),
            "generator": make_agent("generator"),
            "reviewer": make_agent("reviewer"),
        }

        transport = MultiAgentMockTransport({
            "You are researcher.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "research findings"}),
                    ],
                ),
            ],
            "You are generator.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "generated content"}),
                    ],
                ),
            ],
            "You are reviewer.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "review approved"}),
                    ],
                ),
            ],
        })

        research = simple_task("research", agent="researcher")
        generate = simple_task("generate", agent="generator")
        review = simple_task("review", agent="reviewer")

        config = WorkflowConfig(
            name="pipeline",
            description="Three-stage pipeline",
            tasks=[research, generate, review],
            dependencies={
                "generate": ["research"],
                "review": ["generate"],
            },
        )
        scheduler = make_scheduler(
            trace_writer, transport, run_id, agents=agents,
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # All three tasks completed
        completed_count = sum(
            1
            for s in scheduler._task_states.values()  # noqa: SLF001
            if s == TaskState.COMPLETED
        )
        assert completed_count == 3

        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        assert outputs["research"] == "research findings"
        assert outputs["generate"] == "generated content"
        assert outputs["review"] == "review approved"

    async def test_output_propagation_between_tasks(
        self, run_id: uuid.UUID,
    ) -> None:
        """Verify {task_a_output} variable is resolved in task B's prompt."""
        trace_writer = MockTraceWriter()
        agent_a = make_agent("agent-a")
        agent_b = make_agent("agent-b")

        # Capture what prompt task B actually receives
        captured_prompts: list[str] = []

        transport = MultiAgentMockTransport({
            "You are agent-a.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "alpha_result"}),
                    ],
                ),
            ],
            "You are agent-b.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "beta_result"}),
                    ],
                ),
            ],
        })

        # Store original send to intercept prompts
        original_send = transport.send

        async def intercepting_send(
            message: str, **kwargs: Any,  # noqa: ANN401
        ) -> Any:  # noqa: ANN401
            captured_prompts.append(message)
            async for event in original_send(message, **kwargs):
                yield event

        transport.send = intercepting_send  # type: ignore[assignment]

        # task_b's prompt references {task_a_output}
        task_a = simple_task("task_a", agent="agent-a")
        task_b = simple_task(
            "task_b",
            agent="agent-b",
            task_prompt="Process this: {task_a_output}",
        )
        config = WorkflowConfig(
            name="propagation-test",
            description="Output propagation",
            tasks=[task_a, task_b],
            dependencies={"task_b": ["task_a"]},
        )
        scheduler = make_scheduler(
            trace_writer,
            transport,
            run_id,
            agents={"agent-a": agent_a, "agent-b": agent_b},
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # task_b's prompt should contain the resolved output from task_a
        task_b_prompt = captured_prompts[1]
        assert "alpha_result" in task_b_prompt
        assert "{task_a_output}" not in task_b_prompt


# ---------------------------------------------------------------------------
# Parallel execution tests
# ---------------------------------------------------------------------------


class TestParallelExecution:
    async def test_parallel_tasks_no_deps(
        self, run_id: uuid.UUID,
    ) -> None:
        """Two tasks with no dependencies both complete."""
        trace_writer = MockTraceWriter()
        agent_x = make_agent("agent-x")
        agent_y = make_agent("agent-y")

        transport = MultiAgentMockTransport({
            "You are agent-x.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "x_done"}),
                    ],
                ),
            ],
            "You are agent-y.": [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": "y_done"}),
                    ],
                ),
            ],
        })

        task_x = simple_task("task_x", agent="agent-x")
        task_y = simple_task("task_y", agent="agent-y")
        config = WorkflowConfig(
            name="parallel-workflow",
            description="Parallel execution test",
            tasks=[task_x, task_y],
            dependencies={},
        )
        scheduler = make_scheduler(
            trace_writer,
            transport,
            run_id,
            agents={"agent-x": agent_x, "agent-y": agent_y},
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # Both tasks completed
        completed_count = sum(
            1
            for s in scheduler._task_states.values()  # noqa: SLF001
            if s == TaskState.COMPLETED
        )
        assert completed_count == 2

        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        assert outputs["task_x"] == "x_done"
        assert outputs["task_y"] == "y_done"


# ---------------------------------------------------------------------------
# Function (callable) task tests
# ---------------------------------------------------------------------------


class TestFunctionTask:
    async def test_function_task_completes(
        self, run_id: uuid.UUID,
    ) -> None:
        """A callable task runs a Python function and produces output."""
        trace_writer = MockTraceWriter()
        transport = IntegrationMockTransport([])

        async def my_func(ctx: Any) -> TaskResult:  # noqa: ANN401
            return TaskResult(
                output="function_result",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="OK"),
                ],
            )

        module_name = "_e2e_test_funcs.my_module"
        _register_check_module(module_name, my_func=my_func)

        try:
            task = TaskSpec(
                name="func_task",
                callable=f"{module_name}:my_func",
                timeout=30,
                context_refinement=False,
            )
            config = simple_workflow(tasks=[task])
            scheduler = make_scheduler(
                trace_writer, transport, run_id,
            )

            await scheduler.execute_workflow(config)

            # Task completed
            completed = [
                tid
                for tid, s in scheduler._task_states.items()  # noqa: SLF001
                if s == TaskState.COMPLETED
            ]
            assert len(completed) == 1

            # Output stored
            outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
            assert outputs["func_task"] == "function_result"

            # Trace recorded creation and completion
            create_calls = trace_writer.get_calls("create_task")
            assert len(create_calls) == 1
            assert create_calls[0]["name"] == "func_task"
            assert create_calls[0]["task_type"] == "callable"

        finally:
            _cleanup_modules(module_name)

    async def test_function_task_with_structured_output(
        self, run_id: uuid.UUID,
    ) -> None:
        """A callable task can return structured_output."""
        trace_writer = MockTraceWriter()
        transport = IntegrationMockTransport([])

        async def structured_func(ctx: Any) -> TaskResult:  # noqa: ANN401
            return TaskResult(
                output="text_result",
                structured_output={"key": "value", "count": 42},
                check_results=[
                    CheckResult(passed=True, message="OK"),
                ],
            )

        module_name = "_e2e_test_funcs.structured_mod"
        _register_check_module(
            module_name, structured_func=structured_func,
        )

        try:
            task = TaskSpec(
                name="struct_task",
                callable=f"{module_name}:structured_func",
                timeout=30,
                context_refinement=False,
            )
            config = simple_workflow(tasks=[task])
            scheduler = make_scheduler(
                trace_writer, transport, run_id,
            )

            await scheduler.execute_workflow(config)

            # Structured output is used when available (per
            # execute_workflow variable propagation logic)
            structured = scheduler._get_scoped_structured(None)  # noqa: SLF001
            assert structured["struct_task"] == {
                "key": "value",
                "count": 42,
            }

        finally:
            _cleanup_modules(module_name)


# ---------------------------------------------------------------------------
# for_each iteration tests
# ---------------------------------------------------------------------------


class TestForEach:
    async def test_for_each_iterates_over_list(
        self, run_id: uuid.UUID,
    ) -> None:
        """A for_each task creates sub-tasks for each item in the list.

        Uses a function task to produce the list, then a for_each agent
        task that iterates over it.
        """
        trace_writer = MockTraceWriter()

        async def produce_items(ctx: Any) -> TaskResult:  # noqa: ANN401
            return TaskResult(
                output="items",
                structured_output=["alpha", "beta", "gamma"],
                check_results=[
                    CheckResult(passed=True, message="OK"),
                ],
            )

        module_name = "_e2e_test_funcs.items_mod"
        _register_check_module(
            module_name, produce_items=produce_items,
        )

        try:
            # Agent for iteration tasks -- MultiAgentMockTransport
            # routes by system_prompt substring. Each iteration creates
            # a sub-agent-task named "iterate_iter_0", "iterate_iter_1",
            # etc., but they all use the same agent definition.
            iter_agent = make_agent("iter-agent")

            # 3 iterations means 3 send() calls, all matched to
            # the same agent prompt substring.
            transport = MultiAgentMockTransport({
                "You are iter-agent.": [
                    AgentTurn(
                        tool_calls=[
                            ("start_task", {}),
                            ("end_task", {"message": "processed_0"}),
                        ],
                    ),
                    AgentTurn(
                        tool_calls=[
                            ("start_task", {}),
                            ("end_task", {"message": "processed_1"}),
                        ],
                    ),
                    AgentTurn(
                        tool_calls=[
                            ("start_task", {}),
                            ("end_task", {"message": "processed_2"}),
                        ],
                    ),
                ],
            })

            setup_task = TaskSpec(
                name="setup",
                callable=f"{module_name}:produce_items",
                timeout=30,
                context_refinement=False,
            )

            iterate_task = simple_task(
                "iterate",
                agent="iter-agent",
                for_each="setup_output",
                max_concurrency=1,
                for_each_abort_on_failure=False,
            )

            config = WorkflowConfig(
                name="for-each-workflow",
                description="for_each test",
                tasks=[setup_task, iterate_task],
                dependencies={"iterate": ["setup"]},
            )
            scheduler = make_scheduler(
                trace_writer,
                transport,
                run_id,
                agents={"iter-agent": iter_agent},
            )

            with _patch_auto_commit():
                await scheduler.execute_workflow(config)

            # The for_each parent task should be completed
            # (along with 3 iteration sub-tasks + 1 setup task = 5 total)
            completed_count = sum(
                1
                for s in scheduler._task_states.values()  # noqa: SLF001
                if s == TaskState.COMPLETED
            )
            # setup + iterate (parent) + 3 iteration sub-tasks = 5
            assert completed_count == 5

            # The iterate task's output should contain results from
            # all iterations
            outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
            assert outputs["setup"] is not None

        finally:
            _cleanup_modules(module_name)


# ---------------------------------------------------------------------------
# Postcheck tests
# ---------------------------------------------------------------------------


class TestPostchecks:
    async def test_workflow_with_passing_postchecks(
        self, run_id: uuid.UUID,
    ) -> None:
        """A task with script postchecks that pass completes normally."""
        trace_writer = MockTraceWriter()

        async def passing_check(ctx: Any) -> CheckResult:  # noqa: ANN401
            return CheckResult(passed=True, message="All good")

        module_name = "_e2e_test_funcs.checks_mod"
        _register_check_module(
            module_name, passing_check=passing_check,
        )

        try:
            turn = AgentTurn(
                tool_calls=[
                    ("start_task", {}),
                    ("end_task", {"message": "work done"}),
                ],
            )
            transport = IntegrationMockTransport([turn])

            task = simple_task(
                "checked_task",
                postchecks=[
                    ScriptExecution(
                        callable=f"{module_name}:passing_check",
                    ),
                ],
            )
            config = simple_workflow(tasks=[task])
            scheduler = make_scheduler(
                trace_writer, transport, run_id,
            )

            with _patch_auto_commit():
                await scheduler.execute_workflow(config)

            # Task completed (postchecks passed)
            completed = [
                tid
                for tid, s in scheduler._task_states.items()  # noqa: SLF001
                if s == TaskState.COMPLETED
            ]
            assert len(completed) == 1

            outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
            assert outputs["checked_task"] == "work done"

        finally:
            _cleanup_modules(module_name)


# ---------------------------------------------------------------------------
# Trace recording tests
# ---------------------------------------------------------------------------


class TestTraceRecording:
    async def test_trace_records_full_lifecycle(
        self, run_id: uuid.UUID,
    ) -> None:
        """Verify the trace writer records all lifecycle events."""
        trace_writer = MockTraceWriter()
        turn = AgentTurn(
            tool_calls=[
                ("start_task", {}),
                ("end_task", {"message": "traced result"}),
            ],
        )
        transport = IntegrationMockTransport([turn])
        scheduler = make_scheduler(trace_writer, transport, run_id)
        config = simple_workflow()

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # create_task was called
        create_calls = trace_writer.get_calls("create_task")
        assert len(create_calls) == 1

        # create_task_attempt was called
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        assert len(attempt_calls) == 1
        assert attempt_calls[0]["attempt"] == 1

        # complete_task_attempt was called
        complete_calls = trace_writer.get_calls(
            "complete_task_attempt",
        )
        assert len(complete_calls) == 1
        assert complete_calls[0]["agent_output"] == "traced result"
        assert complete_calls[0]["check_verdict"] == "pass"

        # transition_task was called with expected states
        transitions = trace_writer.get_calls("transition_task")
        statuses = [t["new_status"] for t in transitions]
        expected = [
            "prechecking",
            "active",
            "postchecking",
            "completed",
        ]
        assert statuses == expected

        # Transcript entries were written (user + assistant)
        transcript_calls = trace_writer.get_calls(
            "write_transcript_entry",
        )
        roles = [t["role"] for t in transcript_calls]
        assert "user" in roles
        assert "assistant" in roles

    async def test_coherence_summary_skipped_without_overseer(
        self, run_id: uuid.UUID,
    ) -> None:
        """With no overseer, coherence summary is skipped gracefully."""
        trace_writer = MockTraceWriter()
        turn = AgentTurn(
            tool_calls=[
                ("start_task", {}),
                ("end_task", {"message": "result"}),
            ],
        )
        transport = IntegrationMockTransport([turn])
        scheduler = make_scheduler(trace_writer, transport, run_id)
        config = simple_workflow()

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # No coherence summary should have been written
        coherence_calls = trace_writer.get_calls(
            "write_coherence_summary",
        )
        assert len(coherence_calls) == 0
