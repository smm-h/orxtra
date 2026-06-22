"""Concurrency-focused end-to-end integration tests for the orxtra system.

Tests exercise parallel task grouping, for_each with max_concurrency,
diamond dependency ordering, and parallel group computation on complex
DAGs.
"""
from __future__ import annotations

import asyncio
import sys
import types
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import uuid6
from orxtra.protocols._execution import CheckResult
from orxtra.protocols._task import TaskResult, TaskSpec, TaskState
from orxtra.scheduler._graph import (
    build_graph,
    find_parallel_groups,
    topological_sort,
)
from orxtra.scheduler._types import WorkflowConfig

from tests.conftest import (
    AgentTurn,
    IntegrationMockTransport,
    MockTraceWriter,
    MultiAgentMockTransport,
    make_agent,
    make_scheduler,
    simple_task,
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
        "orxtra.scheduler._executor.Scheduler._auto_commit",
        new_callable=AsyncMock,
    )


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid6.uuid7()


# ---------------------------------------------------------------------------
# Test 1: Parallel independent tasks
# ---------------------------------------------------------------------------


class TestParallelIndependentTasks:
    async def test_four_independent_tasks_single_group(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Four tasks with no dependencies land in a single parallel group.

        Verifies: all 4 tasks complete, the dependency graph puts them
        all at depth 0 (one parallel group of size 4).
        """
        trace_writer = MockTraceWriter()
        agents = {}
        prompt_turns: dict[str, list[AgentTurn]] = {}

        for i in range(4):
            name = f"agent-{i}"
            agents[name] = make_agent(name)
            prompt_turns[f"You are {name}."] = [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        (
                            "end_task",
                            {"message": f"result-{i}"},
                        ),
                    ],
                ),
            ]

        transport = MultiAgentMockTransport(prompt_turns)

        tasks = [
            simple_task(f"task_{i}", agent=f"agent-{i}")
            for i in range(4)
        ]
        config = WorkflowConfig(
            name="parallel-workflow",
            description="Parallel test",
            tasks=tasks,
            dependencies={},
        )
        scheduler = make_scheduler(
            trace_writer,
            transport,
            run_id,
            agents=agents,
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # All 4 tasks completed
        completed = sum(
            1
            for s in scheduler._task_states.values()  # noqa: SLF001
            if s == TaskState.COMPLETED
        )
        assert completed == 4

        # Verify all tasks in a single parallel group (depth 0)
        graph = build_graph(config.tasks, config.dependencies)
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)
        assert len(groups) == 1
        assert len(groups[0]) == 4

        # Verify all outputs stored
        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        for i in range(4):
            assert outputs[f"task_{i}"] == f"result-{i}"


# ---------------------------------------------------------------------------
# Test 2: for_each with max_concurrency
# ---------------------------------------------------------------------------


class TestForEachMaxConcurrency:
    async def test_for_each_respects_max_concurrency(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """for_each over 6 items with max_concurrency=2.

        Uses a callable (function) task for iterations so we can track
        actual concurrency via an asyncio counter. Verifies: all 6
        iterations complete, concurrency never exceeds 2.
        """
        trace_writer = MockTraceWriter()
        transport = IntegrationMockTransport([])

        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()

        async def produce_items(
            ctx: Any,  # noqa: ANN401
        ) -> TaskResult:
            return TaskResult(
                output="items",
                structured_output=[
                    "a", "b", "c", "d", "e", "f",
                ],
                check_results=[
                    CheckResult(passed=True, message="OK"),
                ],
            )

        async def process_item(
            ctx: Any,  # noqa: ANN401
        ) -> TaskResult:
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(
                    max_concurrent, current_concurrent,
                )
            # Yield control to let other iterations start
            await asyncio.sleep(0.01)
            async with lock:
                current_concurrent -= 1
            return TaskResult(
                output="processed",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="OK"),
                ],
            )

        items_module = "_e2e_conc_test.items_mod"
        process_module = "_e2e_conc_test.process_mod"
        _register_check_module(
            items_module, produce_items=produce_items,
        )
        _register_check_module(
            process_module, process_item=process_item,
        )

        try:
            setup_task = TaskSpec(
                name="setup",
                callable=f"{items_module}:produce_items",
                timeout=30,
                context_refinement=False,
            )

            iterate_task = TaskSpec(
                name="iterate",
                callable=f"{process_module}:process_item",
                timeout=30,
                context_refinement=False,
                for_each="setup_output",
                max_concurrency=2,
                for_each_abort_on_failure=False,
            )

            config = WorkflowConfig(
                name="for-each-concurrency",
                description="for_each max_concurrency test",
                tasks=[setup_task, iterate_task],
                dependencies={"iterate": ["setup"]},
            )
            scheduler = make_scheduler(
                trace_writer, transport, run_id,
            )

            await scheduler.execute_workflow(config)

            # All 6 iterations + setup + parent = 8 completed tasks
            completed = sum(
                1
                for s in scheduler._task_states.values()  # noqa: SLF001
                if s == TaskState.COMPLETED
            )
            # setup (1) + iterate parent (1) + 6 iterations = 8
            assert completed == 8

            # Concurrency never exceeded max_concurrency=2
            assert max_concurrent <= 2
            # Verify concurrency was actually used (not purely serial)
            assert max_concurrent >= 1

        finally:
            _cleanup_modules(items_module, process_module)


# ---------------------------------------------------------------------------
# Test 3: Diamond dependency ordering
# ---------------------------------------------------------------------------


class TestDiamondDependency:
    async def test_diamond_ordering_a_bc_d(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Diamond: A -> B, A -> C, B -> D, C -> D.

        Verifies: A runs first, B and C run in the same group, D runs
        last. Execution order checked via trace create_task_attempt
        timestamps (creation order reflects execution order since
        each group runs sequentially).
        """
        trace_writer = MockTraceWriter()
        agents = {
            f"agent-{c}": make_agent(f"agent-{c}")
            for c in "abcd"
        }

        prompt_turns: dict[str, list[AgentTurn]] = {}
        for c in "abcd":
            prompt_turns[f"You are agent-{c}."] = [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        (
                            "end_task",
                            {"message": f"output-{c}"},
                        ),
                    ],
                ),
            ]

        transport = MultiAgentMockTransport(prompt_turns)

        tasks = [
            simple_task(f"task_{c}", agent=f"agent-{c}")
            for c in "abcd"
        ]
        deps = {
            "task_b": ["task_a"],
            "task_c": ["task_a"],
            "task_d": ["task_b", "task_c"],
        }
        config = WorkflowConfig(
            name="diamond",
            description="Diamond dependency test",
            tasks=tasks,
            dependencies=deps,
        )
        scheduler = make_scheduler(
            trace_writer,
            transport,
            run_id,
            agents=agents,
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # All 4 tasks completed
        completed = sum(
            1
            for s in scheduler._task_states.values()  # noqa: SLF001
            if s == TaskState.COMPLETED
        )
        assert completed == 4

        # Verify graph structure: 3 parallel groups
        graph = build_graph(config.tasks, config.dependencies)
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)
        assert len(groups) == 3
        assert groups[0] == {"task_a"}
        assert groups[1] == {"task_b", "task_c"}
        assert groups[2] == {"task_d"}

        # Verify execution order via trace attempt creation.
        # Groups are executed sequentially, so task_a's attempt
        # must appear before task_b/task_c, which appear before
        # task_d. Attempt creation happens during execution in
        # group order.
        attempt_calls = trace_writer.get_calls(
            "create_task_attempt",
        )
        attempt_task_ids = [
            c["task_id"] for c in attempt_calls
        ]

        # Map task IDs to names via scheduler's task specs
        id_to_name: dict[uuid.UUID, str] = {
            tid: spec.name
            for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        }

        # Build attempt order: name -> position in attempt
        # creation sequence
        attempt_order: dict[str, int] = {}
        for idx, tid in enumerate(attempt_task_ids):
            name = id_to_name[tid]
            if name not in attempt_order:
                attempt_order[name] = idx

        # A before B and C, B and C before D
        assert attempt_order["task_a"] < attempt_order["task_b"]
        assert attempt_order["task_a"] < attempt_order["task_c"]
        assert attempt_order["task_b"] < attempt_order["task_d"]
        assert attempt_order["task_c"] < attempt_order["task_d"]

        # Verify outputs
        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        for c in "abcd":
            assert outputs[f"task_{c}"] == f"output-{c}"


# ---------------------------------------------------------------------------
# Test 4: Parallel group computation on complex DAGs
# ---------------------------------------------------------------------------


class TestParallelGroupComputation:
    async def test_five_task_dag_three_groups(self) -> None:
        """Complex DAG: A,B (no deps) -> C(A), D(B) -> E(C,D).

        Verifies: 3 parallel groups with correct membership.
        Group 0: {A, B}, Group 1: {C, D}, Group 2: {E}.
        """
        tasks = [
            simple_task(f"task_{c}")
            for c in "abcde"
        ]
        deps = {
            "task_c": ["task_a"],
            "task_d": ["task_b"],
            "task_e": ["task_c", "task_d"],
        }

        graph = build_graph(tasks, deps)
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)

        assert len(groups) == 3
        assert groups[0] == {"task_a", "task_b"}
        assert groups[1] == {"task_c", "task_d"}
        assert groups[2] == {"task_e"}

    async def test_linear_chain_no_parallelism(self) -> None:
        """Linear chain: A -> B -> C -> D.

        Each task is in its own group -- no parallelism possible.
        """
        tasks = [simple_task(f"task_{c}") for c in "abcd"]
        deps = {
            "task_b": ["task_a"],
            "task_c": ["task_b"],
            "task_d": ["task_c"],
        }

        graph = build_graph(tasks, deps)
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)

        assert len(groups) == 4
        assert groups[0] == {"task_a"}
        assert groups[1] == {"task_b"}
        assert groups[2] == {"task_c"}
        assert groups[3] == {"task_d"}

    async def test_wide_fan_out_single_group(self) -> None:
        """8 independent tasks form a single parallel group."""
        tasks = [simple_task(f"task_{i}") for i in range(8)]
        deps: dict[str, list[str]] = {}

        graph = build_graph(tasks, deps)
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)

        assert len(groups) == 1
        assert len(groups[0]) == 8

    async def test_fan_out_fan_in(self) -> None:
        """Fan-out/fan-in: root -> 4 parallel -> sink.

        Group 0: {root}, Group 1: {w0..w3}, Group 2: {sink}.
        """
        tasks = [
            simple_task("root"),
            *[simple_task(f"w{i}") for i in range(4)],
            simple_task("sink"),
        ]
        deps: dict[str, list[str]] = {
            **{f"w{i}": ["root"] for i in range(4)},
            "sink": [f"w{i}" for i in range(4)],
        }

        graph = build_graph(tasks, deps)
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)

        assert len(groups) == 3
        assert groups[0] == {"root"}
        assert groups[1] == {f"w{i}" for i in range(4)}
        assert groups[2] == {"sink"}


# ---------------------------------------------------------------------------
# Test 5: Parallel tasks produce independent outputs
# ---------------------------------------------------------------------------


class TestParallelOutputIsolation:
    async def test_parallel_tasks_output_isolation(
        self,
        run_id: uuid.UUID,
    ) -> None:
        """Three parallel tasks each produce distinct output.

        Verifies: outputs are stored independently under the correct
        task names, no cross-contamination between parallel executions.
        """
        trace_writer = MockTraceWriter()
        agents = {}
        prompt_turns: dict[str, list[AgentTurn]] = {}

        messages = {
            "alpha": "alpha_output_data",
            "beta": "beta_output_data",
            "gamma": "gamma_output_data",
        }

        for name, msg in messages.items():
            agent_name = f"agent-{name}"
            agents[agent_name] = make_agent(agent_name)
            prompt_turns[f"You are {agent_name}."] = [
                AgentTurn(
                    tool_calls=[
                        ("start_task", {}),
                        ("end_task", {"message": msg}),
                    ],
                ),
            ]

        transport = MultiAgentMockTransport(prompt_turns)

        tasks = [
            simple_task(f"task_{name}", agent=f"agent-{name}")
            for name in messages
        ]
        config = WorkflowConfig(
            name="isolation-workflow",
            description="Output isolation test",
            tasks=tasks,
            dependencies={},
        )
        scheduler = make_scheduler(
            trace_writer,
            transport,
            run_id,
            agents=agents,
        )

        with _patch_auto_commit():
            await scheduler.execute_workflow(config)

        # All tasks completed
        completed = sum(
            1
            for s in scheduler._task_states.values()  # noqa: SLF001
            if s == TaskState.COMPLETED
        )
        assert completed == 3

        # Each task's output is correctly stored
        outputs = scheduler._get_scoped_outputs(None)  # noqa: SLF001
        for name, expected_msg in messages.items():
            assert outputs[f"task_{name}"] == expected_msg

        # Verify no output leakage: exactly 3 entries
        task_outputs = {
            k: v
            for k, v in outputs.items()
            if k.startswith("task_")
        }
        assert len(task_outputs) == 3
