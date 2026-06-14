from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class TaskSummary(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: UUID
    name: str
    status: str
    task_type: str
    parent_task_id: UUID | None
    attempt_count: int


class TaskAttempt(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: UUID
    task_id: UUID
    attempt: int
    status: str
    agent_output: str | None
    structured_output: dict[str, Any] | None
    check_result: dict[str, Any] | None
    check_verdict: str | None
    session_id: UUID | None
    input_tokens: int
    output_tokens: int
    reasoning_tokens: int
    cache_read_tokens: int
    cache_write_tokens: int
    cost_usd: Decimal
    duration_seconds: float | None


class RunSummary(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: UUID
    intent: str
    status: str
    created_at: datetime
    finished_at: datetime | None


class RunReport(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: UUID
    intent: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    autonomy_level: str
    config_snapshot: dict[str, Any]
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    total_cost_usd: Decimal
    coherence_summary: str | None
    tasks: list[TaskSummary]
    decisions: list[dict[str, Any]]
    constraints: list[dict[str, Any]]
    assumptions: list[dict[str, Any]]


class InboxItem(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    id: UUID
    run_id: UUID
    status: str
    decision_type: str
    question: str
    options: list[dict[str, Any]]
    assumed_option: str | None
    work_proceeding: str | None
    contradiction_impact: str | None
    tags: list[str]
    deadline: datetime | None
    answer: str | None
    answer_event: str | None
    rejection_reason: str | None
    answered_at: datetime | None
    created_at: datetime


class NotepadEntry(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    run_id: UUID
    task_name: str
    agent_name: str
    entry_type: str
    text: str
    created_at: datetime
