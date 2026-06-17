from __future__ import annotations

import sys
import types

import pytest
from orxt.protocols._execution import CheckResult
from orxt.protocols._task import TaskContext, TaskResult, TaskSpec
from orxt.scheduler._executor import Scheduler
from orxt.scheduler._graph import (
    CycleError,
    build_graph,
    find_parallel_groups,
    topological_sort,
)
from orxt.scheduler._types import WorkflowConfig

from tests.conftest import MockTraceWriter


def _make_task(name: str) -> TaskSpec:
    return TaskSpec(name=name, callable=f"mod:{name}")


class TestBuildGraph:
    def test_builds_adjacency_list(self) -> None:
        tasks = [_make_task("a"), _make_task("b"), _make_task("c")]
        deps = {"b": ["a"], "c": ["a", "b"]}
        graph = build_graph(tasks, deps)
        assert graph == {"a": set(), "b": {"a"}, "c": {"a", "b"}}

    def test_no_dependencies(self) -> None:
        tasks = [_make_task("a"), _make_task("b")]
        graph = build_graph(tasks, {})
        assert graph == {"a": set(), "b": set()}


class TestTopologicalSort:
    def test_linear_chain(self) -> None:
        graph = {"a": set(), "b": {"a"}, "c": {"b"}}
        order = topological_sort(graph)
        assert order == ["a", "b", "c"]

    def test_diamond(self) -> None:
        graph = {"a": set(), "b": {"a"}, "c": {"a"}, "d": {"b", "c"}}
        order = topological_sort(graph)
        assert order.index("a") < order.index("b")
        assert order.index("a") < order.index("c")
        assert order.index("b") < order.index("d")
        assert order.index("c") < order.index("d")

    def test_no_dependencies(self) -> None:
        graph = {"a": set(), "b": set(), "c": set()}
        order = topological_sort(graph)
        assert set(order) == {"a", "b", "c"}

    def test_cycle_detection(self) -> None:
        graph = {"a": {"c"}, "b": {"a"}, "c": {"b"}}
        with pytest.raises(CycleError) as exc_info:
            topological_sort(graph)
        assert len(exc_info.value.cycle) >= 3

    def test_self_cycle(self) -> None:
        graph = {"a": {"a"}}
        with pytest.raises(CycleError) as exc_info:
            topological_sort(graph)
        assert "a" in exc_info.value.cycle

    def test_single_task(self) -> None:
        graph = {"a": set()}
        order = topological_sort(graph)
        assert order == ["a"]

    def test_complex_graph(self) -> None:
        graph = {
            "a": set(),
            "b": set(),
            "c": {"a"},
            "d": {"a", "b"},
            "e": {"c"},
            "f": {"d"},
            "g": {"e", "f"},
            "h": {"g"},
        }
        order = topological_sort(graph)
        assert len(order) == 8
        for node, deps in graph.items():
            for dep in deps:
                assert order.index(dep) < order.index(node)

    def test_empty_graph(self) -> None:
        order = topological_sort({})
        assert order == []

    def test_disconnected_subgraphs(self) -> None:
        graph = {
            "a": set(),
            "b": {"a"},
            "c": {"b"},
            "x": set(),
            "y": {"x"},
            "z": {"y"},
        }
        order = topological_sort(graph)
        assert order.index("a") < order.index("b") < order.index("c")
        assert order.index("x") < order.index("y") < order.index("z")


class TestFindParallelGroups:
    def test_parallel_groups(self) -> None:
        graph = {"a": set(), "b": set(), "c": {"a", "b"}}
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)
        assert len(groups) == 2
        assert groups[0] == {"a", "b"}
        assert groups[1] == {"c"}

    def test_single_task(self) -> None:
        graph = {"a": set()}
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)
        assert groups == [{"a"}]

    def test_empty(self) -> None:
        groups = find_parallel_groups({}, [])
        assert groups == []

    def test_linear_chain_no_parallelism(self) -> None:
        graph = {"a": set(), "b": {"a"}, "c": {"b"}}
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)
        assert len(groups) == 3
        assert groups[0] == {"a"}
        assert groups[1] == {"b"}
        assert groups[2] == {"c"}

    def test_diamond_groups(self) -> None:
        graph = {"a": set(), "b": {"a"}, "c": {"a"}, "d": {"b", "c"}}
        order = topological_sort(graph)
        groups = find_parallel_groups(graph, order)
        assert len(groups) == 3
        assert groups[0] == {"a"}
        assert groups[1] == {"b", "c"}
        assert groups[2] == {"d"}


class TestCompositeWithDependencies:
    """Integration tests for composite task dependency graph execution."""

    async def test_dependency_ordering(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        """A runs before B and C when B and C depend on A."""
        execution_order: list[str] = []

        async def task_a(ctx: TaskContext) -> TaskResult:
            execution_order.append("A")
            return TaskResult(
                output="A done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        async def task_b(ctx: TaskContext) -> TaskResult:
            execution_order.append("B")
            return TaskResult(
                output="B done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        async def task_c(ctx: TaskContext) -> TaskResult:
            execution_order.append("C")
            return TaskResult(
                output="C done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        # Register functions in a temporary module
        import types
        mod = types.ModuleType("_test_dep_funcs")
        mod.task_a = task_a
        mod.task_b = task_b
        mod.task_c = task_c
        import sys
        sys.modules["_test_dep_funcs"] = mod

        try:
            sub_a = TaskSpec(
                name="A",
                callable="_test_dep_funcs:task_a",
            )
            sub_b = TaskSpec(
                name="B",
                callable="_test_dep_funcs:task_b",
                depends_on=["A"],
            )
            sub_c = TaskSpec(
                name="C",
                callable="_test_dep_funcs:task_c",
                depends_on=["A"],
            )

            composite = TaskSpec(
                name="composite",
                subtasks=[sub_a, sub_b, sub_c],
            )

            workflow = WorkflowConfig(
                name="dep-test",
                description="Test dependency ordering",
                tasks=[composite],
                dependencies={},
            )

            await scheduler.execute_workflow(workflow)

            # A must be first
            assert execution_order[0] == "A"
            # B and C come after A (in any order)
            assert set(execution_order[1:]) == {"B", "C"}
        finally:
            del sys.modules["_test_dep_funcs"]

    async def test_no_deps_uses_gather_all(
        self,
        scheduler: Scheduler,
    ) -> None:
        """Composite without depends_on gathers all subtasks."""
        execution_order: list[str] = []

        async def task_x(ctx: TaskContext) -> TaskResult:
            execution_order.append("X")
            return TaskResult(
                output="X done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        async def task_y(ctx: TaskContext) -> TaskResult:
            execution_order.append("Y")
            return TaskResult(
                output="Y done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        import types
        mod = types.ModuleType("_test_nodep_funcs")
        mod.task_x = task_x
        mod.task_y = task_y
        import sys
        sys.modules["_test_nodep_funcs"] = mod

        try:
            sub_x = TaskSpec(
                name="X",
                callable="_test_nodep_funcs:task_x",
            )
            sub_y = TaskSpec(
                name="Y",
                callable="_test_nodep_funcs:task_y",
            )

            composite = TaskSpec(
                name="composite",
                subtasks=[sub_x, sub_y],
            )

            workflow = WorkflowConfig(
                name="nodep-test",
                description="Test no-dep gather",
                tasks=[composite],
                dependencies={},
            )

            await scheduler.execute_workflow(workflow)

            assert set(execution_order) == {"X", "Y"}
        finally:
            del sys.modules["_test_nodep_funcs"]

    async def test_linear_chain(
        self,
        scheduler: Scheduler,
    ) -> None:
        """A -> B -> C executes sequentially."""
        execution_order: list[str] = []

        async def task_a(ctx: TaskContext) -> TaskResult:
            execution_order.append("A")
            return TaskResult(
                output="A done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        async def task_b(ctx: TaskContext) -> TaskResult:
            execution_order.append("B")
            return TaskResult(
                output="B done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        async def task_c(ctx: TaskContext) -> TaskResult:
            execution_order.append("C")
            return TaskResult(
                output="C done",
                structured_output=None,
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        import types
        mod = types.ModuleType("_test_chain_funcs")
        mod.task_a = task_a
        mod.task_b = task_b
        mod.task_c = task_c
        import sys
        sys.modules["_test_chain_funcs"] = mod

        try:
            sub_a = TaskSpec(
                name="A",
                callable="_test_chain_funcs:task_a",
            )
            sub_b = TaskSpec(
                name="B",
                callable="_test_chain_funcs:task_b",
                depends_on=["A"],
            )
            sub_c = TaskSpec(
                name="C",
                callable="_test_chain_funcs:task_c",
                depends_on=["B"],
            )

            composite = TaskSpec(
                name="composite",
                subtasks=[sub_a, sub_b, sub_c],
            )

            workflow = WorkflowConfig(
                name="chain-test",
                description="Test linear chain",
                tasks=[composite],
                dependencies={},
            )

            await scheduler.execute_workflow(workflow)

            assert execution_order == ["A", "B", "C"]
        finally:
            del sys.modules["_test_chain_funcs"]
