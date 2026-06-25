from __future__ import annotations

from collections.abc import Awaitable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from orxtra.protocols._types._enums import Severity
from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable


class ScriptExecution(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    callable: str


class AgentExecution(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    agent: str
    task: str
    block_threshold: Severity
    variables: list[str] = field(default_factory=list)


class CheckVerdict(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    verdict: str
    issues: list[CheckIssue]
    criteria_review: list[CriterionReview]
    summary: str


class CheckIssue(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    severity: Severity
    file: str | None = None
    line_range: tuple[int, int] | None = None
    description: str
    blocking: bool


class CriterionReview(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    criterion: str
    met: bool
    evidence: str


@dataclass(frozen=True)
class CheckResult:
    passed: bool
    message: str
    details: dict[str, Any] | None = None
    fix: Callable[..., Any] | None = None


@dataclass(frozen=True)
class CheckContext:
    variables: dict[str, Any]
    agent_output: str | None
    run_id: uuid.UUID
    session_id: str | None
    task_name: str
    task_id: uuid.UUID
    attempt: int
    parent_task_id: uuid.UUID | None


@dataclass(frozen=True)
class CheckAgentContext:
    task: str
    agent_output: str
    mechanical_results: str
    task_name: str
    attempt: int
    notepad: str


OnSuccessCallback = Awaitable[None]
PreRetryCallback = Awaitable[None]
