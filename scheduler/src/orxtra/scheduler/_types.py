from __future__ import annotations

from enum import StrEnum

from orxtra.protocols import TaskSpec
from pydantic import BaseModel, ConfigDict


class EscalationPolicy(StrEnum):
    CONTINUE_INDEPENDENT = "continue_independent"
    HALT = "halt"
    ABORT_ALL = "abort_all"


class WorkflowConfig(BaseModel):
    """Parsed workflow TOML file."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    description: str
    tasks: list[TaskSpec]
    dependencies: dict[str, list[str]]
    escalation_policy: EscalationPolicy = EscalationPolicy.CONTINUE_INDEPENDENT
    services: list[ServiceConfig] = []


class ServiceConfig(BaseModel):
    """Long-running process declaration."""

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")

    name: str
    start_command: str
    health_check_command: str | None = None
    stop_command: str
    port: int | None = None
    ready_timeout: int = 30


__all__ = [
    "EscalationPolicy",
    "ServiceConfig",
    "WorkflowConfig",
]
