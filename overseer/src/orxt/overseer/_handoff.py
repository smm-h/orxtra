from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from uuid import UUID

    from orxt.session import Session
    from orxt.trace import TraceWriter


async def check_handoff_needed(
    session: Session, model_context_limit: int,
) -> bool:
    used = session.total_input_tokens + session.total_output_tokens
    threshold = int(model_context_limit * 0.9)
    return used >= threshold


async def perform_handoff(
    session: Session,
    trace_writer: TraceWriter,
    run_id: UUID,
) -> Session:
    from orxt.session import create_session  # noqa: PLC0415
    from orxt.transport import Result  # noqa: PLC0415

    summary_parts: list[str] = []
    async for event in session.send(
        "Produce a detailed summary of this conversation for handoff to a new session. "
        "Include: all decisions made, active constraints, current assumptions, "
        "workflow status, and any pending actions."
    ):
        if isinstance(event, Result):
            summary_parts.append(event.text)  # noqa: PERF401

    summary = "".join(summary_parts)

    await trace_writer.write_event(
        run_id=run_id,
        event_type="session_handoff",
        data={
            "old_session_id": session.resume_id(),
            "summary_length": len(summary),
        },
    )

    new_session = create_session(
        transport=session._transport,  # noqa: SLF001
        model=session.model,
        system_prompt=session.system_prompt,
        tools=session.tools,
        trace_writer=trace_writer,
        run_id=run_id,
    )

    async for event in new_session.send(
        f"You are continuing from a previous session. Here is the context summary:\n\n"
        f"{summary}\n\n"
        f"Previous session ID: {session.resume_id()}"
    ):
        if isinstance(event, Result):
            pass

    return new_session
