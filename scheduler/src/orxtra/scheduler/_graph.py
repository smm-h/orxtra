from __future__ import annotations

from collections import deque
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orxtra.protocols._task import TaskSpec


class CycleError(Exception):
    """Raised when the dependency graph contains a cycle."""

    def __init__(self, cycle: list[str]) -> None:
        self.cycle = cycle
        super().__init__(f"Dependency cycle detected: {' -> '.join(cycle)}")


def build_graph(
    tasks: list[TaskSpec], dependencies: dict[str, list[str]]
) -> dict[str, set[str]]:
    """Build adjacency list: task_name -> set of task names it depends on."""
    graph: dict[str, set[str]] = {task.name: set() for task in tasks}
    for task_name, deps in dependencies.items():
        graph[task_name] = set(deps)
    return graph


def topological_sort(graph: dict[str, set[str]]) -> list[str]:
    """Return task names in execution order. Raises CycleError on cycles.

    Uses Kahn's algorithm.
    """
    if not graph:
        return []

    in_degree: dict[str, int] = dict.fromkeys(graph, 0)
    reverse_adj: dict[str, list[str]] = {
        node: [] for node in graph
    }

    for node, deps in graph.items():
        in_degree[node] = len(deps)
        for dep in deps:
            reverse_adj[dep].append(node)

    queue: deque[str] = deque()
    for node, degree in in_degree.items():
        if degree == 0:
            queue.append(node)

    result: list[str] = []
    while queue:
        node = queue.popleft()
        result.append(node)
        for dependent in reverse_adj[node]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    if len(result) != len(graph):
        remaining = set(graph) - set(result)
        cycle = _find_cycle(graph, remaining)
        raise CycleError(cycle)

    return result


def _find_cycle(
    graph: dict[str, set[str]], remaining: set[str]
) -> list[str]:
    """Extract one cycle from the remaining nodes in a graph with cycles."""
    visited: set[str] = set()
    path: list[str] = []
    path_set: set[str] = set()

    def dfs(node: str) -> list[str] | None:
        if node in path_set:
            idx = path.index(node)
            return [*path[idx:], node]
        if node in visited:
            return None
        visited.add(node)
        path.append(node)
        path_set.add(node)
        for dep in graph.get(node, set()):
            if dep in remaining:
                found = dfs(dep)
                if found is not None:
                    return found
        path.pop()
        path_set.discard(node)
        return None

    for node in remaining:
        cycle = dfs(node)
        if cycle is not None:
            return cycle

    return list(remaining)


def find_parallel_groups(
    graph: dict[str, set[str]], order: list[str]
) -> list[set[str]]:
    """Group tasks that can run in parallel (same depth in the DAG)."""
    if not order:
        return []

    depth: dict[str, int] = {}
    for node in order:
        deps = graph.get(node, set())
        if not deps:
            depth[node] = 0
        else:
            depth[node] = max(depth[dep] for dep in deps) + 1

    max_depth = max(depth.values())
    groups: list[set[str]] = [set() for _ in range(max_depth + 1)]
    for node, d in depth.items():
        groups[d].add(node)

    return groups


__all__ = [
    "CycleError",
    "build_graph",
    "find_parallel_groups",
    "topological_sort",
]
