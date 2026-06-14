# Session Module Design

Session lifecycle management on top of transport. Foundation layer.

## Responsibility

Manage the lifecycle of agent sessions. Track session IDs for resumption. Track token counts per session. Provide a clean interface between the scheduler and the transport layer.

## Session Lifecycle

```
create -> send message -> receive events -> (optionally send more messages) -> close
```

A session wraps a transport connection and adds:

1. **Session ID tracking** -- captures the session ID from the first response
2. **Token accumulation** -- sums all token types across all messages
3. **Turn counting** -- tracks how many message exchanges have occurred
4. **Transcript persistence** -- writes every message exchange to PG via the trace module

## Session Object

```python
class Session:
    session_id: str | None
    total_input_tokens: int
    total_output_tokens: int
    total_reasoning_tokens: int
    total_cache_read_tokens: int
    total_cache_write_tokens: int
    turn_count: int

    async def send(self, message: str) -> AsyncIterator[Event]: ...
    def resume_id(self) -> str: ...
```

No `cost_usd` on Session. USD cost is computed at reporting time from token counts and the internal pricing table.

## Session Resumption

After a task completes, the session's `session_id` is recorded in the task attempt. If the task fails and needs a retry, the task's `retry_resume` field controls behavior:
- `retry_resume = true` -- resume the existing session. Cheaper, preserves context.
- `retry_resume = false` -- start fresh. More tokens but more predictable.

This is an explicit choice per task. `retry_resume` is required when `retry > 0`.

## Cross-Restart Resumption

Conversation history is persisted in PostgreSQL via the trace module's `transcripts` table. Sessions can be resumed after a process restart.

## Token Tracking

Each session accumulates from `StepFinish` events:
- `input_tokens`, `output_tokens`, `reasoning_tokens`, `cache_read_tokens`, `cache_write_tokens`

The scheduler sums these across all sessions for total token counts and USD computation.

## Session Factory

```python
def create_session(
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    trace_writer: TraceWriter,
    run_id: uuid.UUID,
    session_id: str | None = None,
) -> Session:
```

## Pricing Table

The `_pricing.py` file maintains the internal pricing table: per-model input/output/cache/reasoning token rates. `compute_cost_usd(model, usage) -> Decimal`. Updated by orxt's developers when provider prices change.

## Files

| File | Contents |
|---|---|
| `_session.py` | `Session` class. Wraps transport, accumulates tokens/turns, tracks session_id, persists transcripts via trace. |
| `_factory.py` | `create_session()` -- constructs a Session. |
| `_pricing.py` | Internal pricing table. `compute_cost_usd(model, usage) -> Decimal`. |

## What This Module Does NOT Do

- Does not choose models (that is agent category resolution)
- Does not filter tools (that is agent permissions)
- Does not manage multiple sessions concurrently (that is the scheduler)
- Does not implement retry logic (that is the scheduler)
