from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from typing import Any

from orxt.protocols._execution import AgentExecution, CheckResult, ScriptExecution
from pydantic import BaseModel, ConfigDict

type Execution = "ScriptExecution | AgentExecution | WorkflowExecution"


class WorkflowExecution(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    description: str
    tasks: list[TaskSpec]
    postchecks: list[Execution]
    budget: Decimal | None = None


class TaskState(StrEnum):
    CREATED = "created"
    PRECHECKING = "prechecking"
    ACTIVE = "active"
    POSTCHECKING = "postchecking"
    COMPLETED = "completed"
    PRECHECK_FAILED = "precheck_failed"
    POSTCHECK_FAILED = "postcheck_failed"
    ESCALATED = "escalated"
    CANCELLED = "cancelled"


class TaskSpec(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    prechecks: list[Execution] = []
    postchecks: list[Execution] = []

    agent: str | None = None
    task_prompt: str | None = None
    callable: str | None = None
    subtasks: list[TaskSpec] | None = None
    wait_for: str | None = None
    decision_point: bool | None = None

    variables: list[str] = []
    depends_on: list[str] | None = None
    category: str | None = None
    timeout: int | None = None
    context_refinement: bool | None = None
    retry: int = 0
    retry_resume: bool | None = None
    retry_inject_failure: bool | None = None
    for_each: str | None = None
    for_each_abort_on_failure: bool | None = None
    max_concurrency: int | None = None
    output_schema: str | None = None
    budget: Decimal | None = None
    write_paths: list[str] | None = None
    on_success: str | None = None
    pre_retry: str | None = None


@dataclass(frozen=True)
class AttemptSummary:
    attempt: int
    output: str | None
    check_results: list[CheckResult]
    duration_seconds: float


@dataclass(frozen=True)
class TaskContext:
    variables: dict[str, Any]
    run_id: uuid.UUID
    task_name: str
    task_id: uuid.UUID
    attempt: int
    prior_attempts: list[AttemptSummary] | None
    notepad_content: str
    parent_task_id: uuid.UUID | None
    nesting_depth: int


@dataclass(frozen=True)
class TaskResult:
    output: str | None
    structured_output: dict[str, Any] | None
    check_results: list[CheckResult]


@dataclass(frozen=True)
class EscalationPayload:
    task_name: str
    task_id: uuid.UUID
    agent_name: str | None
    attempts: int
    failed_checks: list[CheckResult]
    agent_summary: str
    context: TaskContext


WorkflowExecution.model_rebuild()
TaskSpec.model_rebuild()
