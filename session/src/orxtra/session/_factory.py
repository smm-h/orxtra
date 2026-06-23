from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncpg
    from orxtra.protocols import Tool
    from orxtra.trace import StorageBackend, TraceWriter
    from orxtra.transport import Transport

from orxtra.session._session import Session


def _transcript_to_messages(
    transcript: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Convert transcript entries into simple role/content messages.

    Rebuilds conversation history suitable for injection into
    Transport._sessions. Tool call details are appended to the
    assistant content so the LLM retains context about what
    tools were used.
    """
    messages: list[dict[str, Any]] = []
    for entry in transcript:
        role = entry["role"]
        content = entry.get("content", "")
        if role == "assistant" and entry.get("tool_calls"):
            # Append tool call summaries so the LLM sees what
            # tools were used in prior turns
            calls = entry["tool_calls"]
            call_list = calls.get("calls", []) if isinstance(calls, dict) else []
            if call_list:
                parts = [content] if content else []
                for call in call_list:
                    name = call.get("tool_name", "unknown")
                    output = call.get("output", "")
                    parts.append(f"[Tool: {name} -> {output}]")
                content = "\n".join(parts)
        messages.append({"role": role, "content": content})
    return messages


async def create_session(  # noqa: PLR0913
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    trace_writer: TraceWriter | StorageBackend,
    run_id: uuid.UUID,
    session_id: str | None = None,
    pool: asyncpg.Pool | None = None,
    backend: StorageBackend | None = None,
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

    if session_id is not None and (backend is not None or pool is not None):
        sid = uuid.UUID(session_id) if isinstance(session_id, str) else session_id
        # Prefer StorageBackend for reads; fall back to pool-based functions
        if backend is not None:
            rows = await backend.read_session_token_counts(sid)
            for row in rows:
                tokens = row["tokens"]
                if tokens:
                    session.total_input_tokens += tokens.get("input_tokens", 0)
                    session.total_output_tokens += tokens.get("output_tokens", 0)
                    session.total_reasoning_tokens += tokens.get("reasoning_tokens", 0)
                    session.total_cache_read_tokens += tokens.get("cache_read_tokens", 0)
                    session.total_cache_write_tokens += tokens.get("cache_write_tokens", 0)
            session.turn_count = await backend.read_session_turn_count(sid)

            # Load conversation history and inject into transport
            transcript = await backend.read_transcript(sid)
            if transcript:
                messages = _transcript_to_messages(transcript)
                transport.inject_history(session_id, messages)

        elif pool is not None:
            from orxtra.trace import (  # noqa: PLC0415
                read_session_token_counts,
                read_session_turn_count,
                read_transcript,
            )

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

            # Load conversation history and inject into transport
            transcript = await read_transcript(pool, sid)
            if transcript:
                messages = _transcript_to_messages(transcript)
                transport.inject_history(session_id, messages)

    return session
