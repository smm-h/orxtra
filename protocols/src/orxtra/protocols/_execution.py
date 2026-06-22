from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict

if TYPE_CHECKING:
    from collections.abc import Callable


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    NIT = "nit"


SEVERITY_ORDER: dict[Severity, int] = {
    Severity.CRITICAL: 4,
    Severity.MAJOR: 3,
    Severity.MINOR: 2,
    Severity.NIT: 1,
}


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
