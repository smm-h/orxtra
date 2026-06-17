from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orxt.protocols import Tool
    from orxt.trace import TraceWriter
    from orxt.transport import Transport

from orxt.session._session import Session


async def create_session(  # noqa: PLR0913
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    trace_writer: TraceWriter,
    run_id: uuid.UUID,
    session_id: str | None = None,
    pool: object = None,
) -> Session:
    session = Session(
        transport=transport,
        model=model,
        system_prompt=system_prompt,
        tools=tools,
        trace_writer=trace_writer,
        run_id=run_id,
        session_id=session_id,
    )

    if session_id is not None and pool is not None:
        sid = uuid.UUID(session_id) if isinstance(session_id, str) else session_id
        rows = await pool.fetch(
            "SELECT tokens FROM transcripts"
            " WHERE session_id = $1 AND tokens IS NOT NULL",
            sid,
        )
        for row in rows:
            tokens = row["tokens"]
            if tokens:
                session.total_input_tokens += tokens.get("input_tokens", 0)
                session.total_output_tokens += tokens.get("output_tokens", 0)
                session.total_reasoning_tokens += tokens.get("reasoning_tokens", 0)
                session.total_cache_read_tokens += tokens.get("cache_read_tokens", 0)
                session.total_cache_write_tokens += tokens.get("cache_write_tokens", 0)
        turn_count = await pool.fetchval(
            "SELECT COUNT(*) FROM transcripts WHERE session_id = $1",
            sid,
        )
        session.turn_count = turn_count or 0

    return session
