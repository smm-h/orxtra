from __future__ import annotations

from typing import TYPE_CHECKING

from orxt.overseer._tools import (
    make_add_constraint_tool,
    make_create_inbox_item_tool,
    make_record_assumption_tool,
    make_record_decision_tool,
    make_update_workflow_status_tool,
    make_write_lesson_tool,
)
from orxt.protocols import format_event
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

if TYPE_CHECKING:
    from uuid import UUID

    from orxt.overseer._autonomy import AutonomyLevel
    from orxt.overseer._health import HealthMonitor
    from orxt.protocols._tool import Tool
    from orxt.session import Session
    from orxt.trace import TraceWriter

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


class Overseer:
    def __init__(
        self,
        session: Session,
        trace_writer: TraceWriter,
        run_id: UUID,
        autonomy_level: AutonomyLevel,
        health_monitor: HealthMonitor,
    ) -> None:
        self._session = session
        self._trace_writer = trace_writer
        self._run_id = run_id
        self._autonomy_level = autonomy_level
        self._health_monitor = health_monitor

    @property
    def session(self) -> Session:
        return self._session

    @session.setter
    def session(self, value: Session) -> None:
        self._session = value

    async def handle_event(self, event: OverseerEvent) -> None:
        message = format_event(event)
        async for _ev in self._session.send(message):
            pass

    def get_tools(self) -> list[Tool]:
        return [
            make_record_decision_tool(self._trace_writer, self._run_id),
            make_add_constraint_tool(self._trace_writer, self._run_id),
            make_record_assumption_tool(self._trace_writer, self._run_id),
            make_create_inbox_item_tool(
                self._trace_writer, self._run_id,
            ),
            make_write_lesson_tool(self._trace_writer, self._run_id),
            make_update_workflow_status_tool(self._trace_writer),
        ]
