# Session Module Design

Session lifecycle management on top of transport.

## Responsibility

Manage the lifecycle of agent sessions. Track session IDs for resumption. Track token counts per session. Provide a clean interface between the scheduler and the transport layer.

## Session Lifecycle

```
create -> send message -> receive events -> (optionally send more messages) -> close
```

A session wraps a transport connection and adds:

1. **Session ID tracking** -- captures the session ID from the first response and makes it available for resumption
2. **Token accumulation** -- sums token counts across all messages in the session
3. **Turn counting** -- tracks how many message exchanges have occurred
4. **Transcript persistence** -- writes every message exchange to PG via the trace module

## Session Object

```python
class Session:
    session_id: str | None     # populated after first send()
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    turn_count: int

    async def send(self, message: str) -> AsyncIterator[Event]:
        """Send a message and stream events back."""
        ...

    def resume_id(self) -> str:
        """Return the session ID for resumption. Raises if no session yet."""
        ...
```

No `cost_usd` on Session. USD cost is computed at reporting time from token counts and the internal pricing table.

## Session Resumption

The key mechanism for efficient retries and multi-turn conversations:

1. After a step completes, the session's `session_id` is recorded in the step attempt
2. If the step fails and needs a retry, the step's `retry_resume` field controls behavior:
   - `retry_resume = true` -- **Resume** the existing session. Cheaper, preserves context.
   - `retry_resume = false` -- **Start fresh**. Clean slate. More tokens but more predictable.

This is an explicit choice per workflow step. `retry_resume` is required when `retry > 0`.

## Cross-Restart Resumption

Conversation history is persisted in PostgreSQL via the trace module's `transcripts` table. This means sessions can be resumed after a process restart -- the transport reconstructs the message history from PG. This resolves the earlier design ambiguity where conversation history was in-memory only.

## Token Tracking

Each session accumulates token counts from `StepFinish` events:

| Field | Source |
|---|---|
| `input_tokens` | `StepFinish.input_tokens` |
| `output_tokens` | `StepFinish.output_tokens` |
| `reasoning_tokens` | `StepFinish.reasoning_tokens` |
| `cache_read_tokens` | `StepFinish.cache_read_tokens` |
| `cache_write_tokens` | `StepFinish.cache_write_tokens` |

The scheduler sums these across all sessions in a run for total token counts in the run report. USD is computed from these counts using the internal pricing table.

## Session Factory

```python
def create_session(
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    trace_writer: TraceWriter,
    run_id: uuid.UUID,
    session_id: str | None = None,  # for resumption
) -> Session:
    ...
```

- `model` is required -- already resolved from category
- `system_prompt` is required -- already loaded and substituted
- `tools` is required -- already filtered by permissions
- `trace_writer` is required -- for transcript persistence
- `run_id` is required -- for associating transcripts with the run
- `session_id` is optional -- pass it to resume an existing session

## Files

| File | Contents |
|---|---|
| `_session.py` | `Session` class. Wraps transport, accumulates tokens/turns, tracks session_id, persists transcripts via trace, exposes `send()` and `resume_id()`. |
| `_factory.py` | `create_session(transport, model, system_prompt, tools, trace_writer, run_id, session_id?)` -- constructs a `Session`. |

## What This Module Does NOT Do

- Does not choose models (that is agent/ category resolution)
- Does not filter tools (that is agent/ permissions)
- Does not manage multiple sessions concurrently (that is scheduler/)
- Does not implement retry logic (that is scheduler/)
- Does not own transcript persistence format (that's trace/)
- Does not compute USD costs (that's computed at reporting time)
- Does not enforce cost limits (the scheduler checks budget and decides)
