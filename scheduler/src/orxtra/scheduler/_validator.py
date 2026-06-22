from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.scheduler._graph import CycleError, build_graph, topological_sort

if TYPE_CHECKING:
    from orxtra.protocols._task import TaskSpec
    from orxtra.scheduler._types import WorkflowConfig

_OUTPUT_SUFFIXES = ("_output", "_text", "_result")


def _count_execution_modes(task: TaskSpec) -> int:
    """Count how many execution modes are set on a task."""
    count = 0
    if task.agent is not None or task.task_prompt is not None:
        count += 1
    if task.callable is not None:
        count += 1
    if task.subtasks is not None:
        count += 1
    if task.wait_for is not None:
        count += 1
    if task.decision_point is not None:
        count += 1
    return count


def _has_agent_mode(task: TaskSpec) -> bool:
    return task.agent is not None and task.task_prompt is not None


def _validate_agent_fields(
    task: TaskSpec, errors: list[str]
) -> None:
    """Validate agent-specific required fields."""
    if task.agent is not None and task.task_prompt is None:
        errors.append(
            f"Task '{task.name}' has 'agent' but missing 'task_prompt'"
        )
    if task.task_prompt is not None and task.agent is None:
        errors.append(
            f"Task '{task.name}' has 'task_prompt' but missing 'agent'"
        )
    if _has_agent_mode(task):
        if task.timeout is None:
            errors.append(
                f"Agent task '{task.name}' missing 'timeout'"
            )
        if task.context_refinement is None:
            errors.append(
                f"Agent task '{task.name}'"
                " missing 'context_refinement'"
            )


def _validate_conditional_fields(
    task: TaskSpec, errors: list[str]
) -> None:
    """Validate fields required conditionally (retry, for_each)."""
    if task.retry > 0:
        if task.retry_resume is None:
            errors.append(
                f"Task '{task.name}' has retry > 0"
                " but missing 'retry_resume'"
            )
        if task.retry_inject_failure is None:
            errors.append(
                f"Task '{task.name}' has retry > 0"
                " but missing 'retry_inject_failure'"
            )

    if task.for_each is not None:
        if task.for_each_abort_on_failure is None:
            errors.append(
                f"Task '{task.name}' has 'for_each'"
                " but missing 'for_each_abort_on_failure'"
            )
        if task.max_concurrency is None:
            errors.append(
                f"Task '{task.name}' has 'for_each'"
                " but missing 'max_concurrency'"
            )


def _validate_task(
    task: TaskSpec, errors: list[str], seen_names: set[str]
) -> None:
    """Validate a single task, recursing into subtasks."""
    mode_count = _count_execution_modes(task)

    if mode_count == 0:
        errors.append(f"Task '{task.name}' has no execution mode")
    elif mode_count > 1:
        errors.append(
            f"Task '{task.name}' has multiple execution modes"
        )

    _validate_agent_fields(task, errors)
    _validate_conditional_fields(task, errors)

    if task.name in seen_names:
        errors.append(f"Duplicate task name '{task.name}'")
    seen_names.add(task.name)

    if task.subtasks is not None:
        sub_names: set[str] = set()
        for sub in task.subtasks:
            _validate_task(sub, errors, sub_names)


def _validate_dependencies(
    config: WorkflowConfig, errors: list[str]
) -> None:
    """Validate dependency references and cycles."""
    task_names = {task.name for task in config.tasks}

    for source_name, deps in config.dependencies.items():
        if source_name not in task_names:
            errors.append(
                f"Dependency source '{source_name}'"
                " not found in tasks"
            )
        errors.extend(
            f"Dependency target '{dep}' not found in tasks"
            for dep in deps
            if dep not in task_names
        )

    graph = build_graph(config.tasks, config.dependencies)
    try:
        topological_sort(graph)
    except CycleError as e:
        errors.append(
            f"Dependency cycle: {' -> '.join(e.cycle)}"
        )


def _validate_variable_collisions(
    config: WorkflowConfig, errors: list[str]
) -> None:
    """Check for variable name collisions."""
    output_vars: set[str] = set()
    for task in config.tasks:
        for suffix in _OUTPUT_SUFFIXES:
            output_vars.add(f"{task.name}{suffix}")

    all_workflow_vars: set[str] = set()
    for task in config.tasks:
        for var in task.variables:
            all_workflow_vars.add(var)

    collisions = output_vars & all_workflow_vars
    errors.extend(
        f"Variable name collision: '{var}'"
        " is both a task output and a workflow variable"
        for var in sorted(collisions)
    )


def validate_task_tree(config: WorkflowConfig) -> list[str]:
    """Validate a workflow config.

    Returns list of error messages (empty = valid).
    """
    errors: list[str] = []

    seen_names: set[str] = set()
    for task in config.tasks:
        _validate_task(task, errors, seen_names)

    _validate_dependencies(config, errors)
    _validate_variable_collisions(config, errors)

    return errors


__all__ = [
    "validate_task_tree",
]
