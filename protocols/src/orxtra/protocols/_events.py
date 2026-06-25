from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import uuid

    from orxtra.protocols._task import EscalationPayload


@dataclass(frozen=True)
class RunStarted:
    intent: str
    config_snapshot: dict[str, Any]


@dataclass(frozen=True)
class TaskFailed:
    task_id: uuid.UUID
    task_name: str
    payload: EscalationPayload


@dataclass(frozen=True)
class TaskEscalated:
    task_id: uuid.UUID
    task_name: str
    from_child_task_id: uuid.UUID
    payload: EscalationPayload


@dataclass(frozen=True)
class BudgetThresholdCrossed:
    workflow_id: uuid.UUID
    budget_usd: Decimal
    spent_usd: Decimal
    threshold_pct: float


@dataclass(frozen=True)
class BudgetExhausted:
    workflow_id: uuid.UUID


@dataclass(frozen=True)
class InboxAnswered:
    item_id: uuid.UUID
    assumed_option: str
    actual_answer: str
    contradicts: bool


@dataclass(frozen=True)
class InboxRejected:
    item_id: uuid.UUID
    rejection_reason: str


@dataclass(frozen=True)
class StructuralAdvisory:
    task_id: uuid.UUID
    observation: str
    suggestion: str


@dataclass(frozen=True)
class HealthDegraded:
    event_type: str
    failure_rate: float
    threshold: float

