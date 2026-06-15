from __future__ import annotations

import uuid
from decimal import Decimal
from typing import Any

from orxt.protocols._constraints import ConstraintTier
from orxt.protocols._task import Execution
from pydantic import BaseModel, ConfigDict


class CreateWorkflowParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    description: str
    goals: list[str]
    postchecks: list[Execution] = []
    budget: Decimal | None = None


class CreateWorkflowResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    workflow_id: uuid.UUID


class CreateTaskParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    agent: str
    task_prompt: str
    prechecks: list[Execution] = []
    postchecks: list[Execution] = []
    variable_values: dict[str, str] = {}
    timeout: int
    context_refinement: bool
    budget: Decimal | None = None
    write_paths: list[str] | None = None
    category: str | None = None
    retry: int = 0
    retry_resume: bool | None = None
    retry_inject_failure: bool | None = None
    depends_on: list[str] | None = None


class CreateTaskResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    task_id: uuid.UUID


class CreateWaitForParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    event_name: str
    timeout: int
    depends_on: list[str] | None = None


class CreateWaitForResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    task_id: uuid.UUID


class RecordDecisionParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    decision_type: str
    choice: dict[str, Any]
    rationale: str | None = None


class RecordDecisionResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    decision_id: uuid.UUID


class AddConstraintParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    kind: str
    text: str
    tier: ConstraintTier
    args: dict[str, Any] | None = None


class AddConstraintResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    constraint_id: uuid.UUID


class RecordAssumptionParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    text: str
    scope: str
    create_inbox_item: bool


class RecordAssumptionResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    assumption_id: uuid.UUID
    inbox_item_id: uuid.UUID | None = None


class CreateInboxItemParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    decision_type: str
    question: str
    options: list[dict[str, Any]]
    assumed_option: str
    work_proceeding: str
    contradiction_impact: str
    tags: list[str] = []
    deadline: str | None = None
    answer_event: str | None = None


class CreateInboxItemResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    item_id: uuid.UUID


class WriteLessonParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    text: str
    relevance_tags: list[str]
    permanent: bool
    source_files: list[str] = []


class WriteLessonResult(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    lesson_id: uuid.UUID


class UpdateWorkflowStatusParams(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    workflow_id: str
    current_step: str | None = None
    health: str
