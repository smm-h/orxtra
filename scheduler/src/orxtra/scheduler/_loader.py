from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Any

from orxtra.protocols import (
    AgentExecution,
    Execution,
    ScriptExecution,
    Severity,
    TaskSpec,
)
from orxtra.scheduler._gen_workflow import validate_bytes as _validate_workflow_document
from orxtra.scheduler._types import EscalationPolicy, ServiceConfig, WorkflowConfig


class WorkflowValidationError(ValueError):
    """A workflow document failed strictspec validation at the load boundary.

    Subclasses ValueError so existing ``except ValueError`` handlers keep
    working. Carries the rendered strictspec diagnostics.
    """


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


def _document_text(source: Path | str) -> str:
    """Return the TOML text for a file path or a raw TOML string."""
    if isinstance(source, Path):
        return source.read_text()
    return source


def _gate_document(text: str, source: Path | str) -> None:
    """Run the strictspec document gate at the load boundary.

    The generated validator enforces the document shape (required
    [workflow] name/description, at least one task, per-task field types,
    unknown-key rejection) and the intra-document constraints (exactly-one
    execution mode, agent-mode co-presence, conditional-required retry and
    for_each fields, depends_on reference resolution). A failing document is
    a hard error. This subsumes the former hand-rolled ``_validate_structure``
    and the execution-mode/agent/conditional shape checks. Cross-document and
    graph-shaped checks (top-level dependency-map resolution, DAG acyclicity,
    variable collisions, headless mode) stay consumer-native downstream.
    """
    _root, diags = _validate_workflow_document(text.encode("utf-8"), "toml")
    if diags:
        where = f" ({source})" if isinstance(source, Path) else ""
        detail = "\n".join(
            f"  {d.code} at {d.path}: {d.message}" for d in diags
        )
        msg = f"Invalid workflow document{where}:\n{detail}"
        raise WorkflowValidationError(msg)


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
    text = _document_text(source)
    _gate_document(text, source)
    data = tomllib.loads(text)

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

    # Parse services
    raw_services: list[dict[str, Any]] = data.get("services", [])
    services = [ServiceConfig(**svc) for svc in raw_services]

    escalation_policy_str = workflow_section.get("escalation_policy")

    kwargs: dict[str, Any] = {
        "name": workflow_section["name"],
        "description": workflow_section["description"],
        "tasks": updated_tasks,
        "dependencies": dependencies,
        "services": services,
    }
    if escalation_policy_str is not None:
        kwargs["escalation_policy"] = EscalationPolicy(escalation_policy_str)

    return WorkflowConfig(**kwargs)


__all__ = [
    "WorkflowValidationError",
    "load_workflow",
]
