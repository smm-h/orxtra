from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg
    from orxtra.protocols import Tool
    from orxtra.trace import TraceWriter
    from orxtra.transport import Transport

from orxtra.session._session import Session


async def create_session(  # noqa: PLR0913
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    trace_writer: TraceWriter,
    run_id: uuid.UUID,
    session_id: str | None = None,
    pool: asyncpg.Pool | None = None,
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
        from orxtra.trace import read_session_token_counts, read_session_turn_count

        sid = uuid.UUID(session_id) if isinstance(session_id, str) else session_id
        rows = await read_session_token_counts(pool, sid)
        for row in rows:
            tokens = row["tokens"]
            if tokens:
                session.total_input_tokens += tokens.get("input_tokens", 0)
                session.total_output_tokens += tokens.get("output_tokens", 0)
                session.total_reasoning_tokens += tokens.get("reasoning_tokens", 0)
                session.total_cache_read_tokens += tokens.get("cache_read_tokens", 0)
                session.total_cache_write_tokens += tokens.get("cache_write_tokens", 0)
        session.turn_count = await read_session_turn_count(pool, sid)

    return session
