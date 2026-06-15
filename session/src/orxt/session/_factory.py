from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import uuid

    from orxt.protocols import Tool
    from orxt.trace import TraceWriter
    from orxt.transport import Transport

from orxt.session._session import Session


def create_session(  # noqa: PLR0913
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    trace_writer: TraceWriter,
    run_id: uuid.UUID,
    session_id: str | None = None,
) -> Session:
    return Session(
        transport=transport,
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        trace_writer=trace_writer,
        run_id=run_id,
        session_id=session_id,
    )
