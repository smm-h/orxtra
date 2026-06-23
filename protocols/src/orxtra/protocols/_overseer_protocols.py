from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

from orxtra.protocols._events import (
    BudgetExhausted,
    BudgetThresholdCrossed,
    HealthDegraded,
    InboxAnswered,
    InboxRejected,
    RunStarted,
    StructuralAdvisory,
    TaskEscalated,
    TaskFailed,
)

if TYPE_CHECKING:
    from orxtra.session import Session

type OverseerEvent = (
    RunStarted
    | TaskFailed
    | TaskEscalated
    | BudgetThresholdCrossed
    | BudgetExhausted
    | InboxAnswered
    | InboxRejected
    | StructuralAdvisory
    | HealthDegraded
)


@runtime_checkable
class OverseerProtocol(Protocol):
    """Protocol for the Overseer, used by the scheduler to avoid
    a direct dependency on the intelligence layer."""

    session: Session

    async def handle_event(self, event: OverseerEvent) -> None: ...


@runtime_checkable
class HealthMonitorProtocol(Protocol):
    """Protocol for health monitoring, used by the scheduler to avoid
    a direct dependency on the intelligence layer."""

    def is_degraded(self, event_type: str) -> bool: ...

    def record_event(
        self, event_type: str, *, success: bool, is_repetition: bool = False,
    ) -> None: ...
