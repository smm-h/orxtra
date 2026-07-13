from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from datetime import datetime
    from uuid import UUID

    from orxtra.trace import TraceWriter


async def create_escalation_inbox(
    trace_writer: TraceWriter,
    run_id: UUID,
    question: str,
    options: list[dict[str, Any]],
    assumed: str,
    work_proceeding: str,
    contradiction_impact: str,
    deadline: datetime | None = None,
) -> UUID:
    return await trace_writer.create_inbox_item(
        run_id=run_id,
        decision_type="escalation",
        question=question,
        options=options,
        assumed_option=assumed,
        work_proceeding=work_proceeding,
        contradiction_impact=contradiction_impact,
        deadline=deadline,
    )
