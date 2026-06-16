from __future__ import annotations

import dataclasses
import json
from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

if TYPE_CHECKING:
    import uuid

    from orxt.protocols._task import EscalationPayload


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


def _to_json_safe(value: object) -> object:  # noqa: PLR0911
    """Recursively convert a value to JSON-serializable form."""
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, UUID):
        return value.hex
    if isinstance(value, Decimal):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result: dict[str, object] = {}
        for f in dataclasses.fields(value):
            field_value = getattr(value, f.name)
            if callable(field_value):
                continue
            result[f.name] = _to_json_safe(field_value)
        return result
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    return str(value)


def format_event(event: object) -> str:
    """Format an event dataclass as a JSON string for the Overseer.

    Handles UUID, Decimal, nested dataclasses, and other non-primitive types
    by converting them to JSON-safe representations.
    """
    event_type = type(event).__name__
    fields: dict[str, object] = {}
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        for f in dataclasses.fields(event):
            val = getattr(event, f.name)
            fields[f.name] = _to_json_safe(val)
    return json.dumps({"event_type": event_type, **fields})
