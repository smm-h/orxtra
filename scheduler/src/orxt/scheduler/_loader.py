from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from orxt.protocols._execution import AgentExecution, ScriptExecution, Severity
from orxt.protocols._task import Execution, TaskSpec
from orxt.scheduler._types import WorkflowConfig


def _parse_postchecks(raw: dict[str, Any]) -> list[Execution]:
    """Convert TOML postchecks section to list of Execution objects."""
    result: list[Execution] = [
        ScriptExecution(callable=script)
        for script in raw.get("scripts", [])
    ]
    result.extend(
        AgentExecution(
            agent=agent_def["agent"],
            task=agent_def["task"],
            block_threshold=Severity(agent_def["block_threshold"]),
            variables=agent_def.get("variables", []),
        )
        for agent_def in raw.get("agents", [])
    )
    return result


def _parse_task(raw: dict[str, Any]) -> TaskSpec:
    """Parse a single task dict from TOML into a TaskSpec."""
    fields: dict[str, Any] = {}

    direct_fields = [
        "name",
        "agent",
        "task_prompt",
        "callable",
        "wait_for",
        "decision_point",
        "variables",
        "depends_on",
        "category",
        "timeout",
        "context_refinement",
        "retry",
        "retry_resume",
        "retry_inject_failure",
        "for_each",
        "for_each_abort_on_failure",
        "max_concurrency",
        "output_schema",
        "budget",
        "write_paths",
        "on_success",
        "pre_retry",
    ]

    for field in direct_fields:
        if field in raw:
            fields[field] = raw[field]

    if "prechecks" in raw:
        fields["prechecks"] = _parse_postchecks(raw["prechecks"])

    if "postchecks" in raw:
        fields["postchecks"] = _parse_postchecks(raw["postchecks"])

    if "subtasks" in raw:
        fields["subtasks"] = [
            _parse_task(sub) for sub in raw["subtasks"]
        ]

    return TaskSpec(**fields)


def _parse_toml(source: Path | str) -> dict[str, Any]:
    """Parse TOML from a file path or raw string."""
    if isinstance(source, Path):
        return tomllib.loads(source.read_text())
    return tomllib.loads(source)


def _validate_structure(data: dict[str, Any]) -> None:
    """Validate the top-level TOML structure."""
    if "workflow" not in data:
        msg = "Missing [workflow] section"
        raise ValueError(msg)

    workflow_section = data["workflow"]
    if "name" not in workflow_section:
        msg = "Missing 'name' in [workflow] section"
        raise ValueError(msg)

    if "description" not in workflow_section:
        msg = "Missing 'description' in [workflow] section"
        raise ValueError(msg)

    if not data.get("tasks"):
        msg = "Workflow must have at least one task"
        raise ValueError(msg)


def _validate_dependencies(
    dependencies: dict[str, list[str]], task_names: set[str]
) -> None:
    """Validate that all dependency references exist."""
    for source_name, deps in dependencies.items():
        if source_name not in task_names:
            msg = (
                f"Dependency source '{source_name}'"
                " does not match any task"
            )
            raise ValueError(msg)
        for dep in deps:
            if dep not in task_names:
                msg = (
                    f"Dependency target '{dep}'"
                    " does not match any task"
                )
                raise ValueError(msg)


def load_workflow(source: Path | str) -> WorkflowConfig:
    """Load a workflow from a TOML file path or TOML string.

    Args:
        source: A Path to a .toml file, or a raw TOML string.

    Returns:
        Parsed and validated WorkflowConfig.

    Raises:
        ValueError: If the TOML is invalid or fails validation.
    """
    data = _parse_toml(source)
    _validate_structure(data)

    workflow_section = data["workflow"]
    raw_tasks: list[dict[str, Any]] = data["tasks"]
    tasks = [_parse_task(t) for t in raw_tasks]
    task_names = {t.name for t in tasks}

    dependencies: dict[str, list[str]] = data.get(
        "dependencies", {}
    )
    _validate_dependencies(dependencies, task_names)

    updated_tasks = [
        task.model_copy(update={"depends_on": dependencies[task.name]})
        if task.name in dependencies
        else task
        for task in tasks
    ]

    return WorkflowConfig(
        name=workflow_section["name"],
        description=workflow_section["description"],
        tasks=updated_tasks,
        dependencies=dependencies,
    )


__all__ = [
    "load_workflow",
]
