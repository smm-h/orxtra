from __future__ import annotations

import pytest
from orxt.protocols._task import TaskSpec
from orxt.scheduler._graph import (
    CycleError,
    build_graph,
    find_parallel_groups,
    topological_sort,
)


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
