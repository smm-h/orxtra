from __future__ import annotations

from typing import Protocol

from orxt.protocols._events import (
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


class OverseerInterface(Protocol):
    """Interface the scheduler uses to communicate
    with the Overseer."""

    async def send_event(
        self, event: OverseerEvent,
    ) -> None: ...
    async def verify_actions(self) -> list[str]: ...
