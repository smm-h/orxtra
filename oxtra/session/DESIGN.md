# Session Module Design

Session lifecycle management on top of transport.

## Responsibility

Manage the lifecycle of agent sessions. Track session IDs for resumption. Track costs (tokens and USD). Provide a clean interface between the pipeline executor and the transport layer.

## Session Lifecycle

```
create -> send message -> receive events -> (optionally send more messages) -> close
```

A session wraps a transport connection and adds:

1. **Session ID tracking** -- captures the session ID from the first response and makes it available for resumption
2. **Cost accumulation** -- sums tokens and costs across all messages in the session
3. **Turn counting** -- tracks how many message exchanges have occurred

## Session Object

```python
class Session:
    session_id: str | None     # populated after first send()
    total_input_tokens: int
    total_output_tokens: int
    total_cost_usd: float
    turn_count: int

    async def send(self, message: str) -> AsyncIterator[Event]:
        """Send a message and stream events back."""
        ...

    def resume_id(self) -> str:
        """Return the session ID for resumption. Raises if no session yet."""
        ...
```

## Session Resumption

The key mechanism for efficient retries and multi-turn conversations:

1. After a step completes, the session's `session_id` is recorded in the pipeline's step result
2. If the step fails and needs a retry with `retry_inject_failure`, the executor can either:
   - **Resume** the existing session (continue the conversation with failure context) -- cheaper, preserves full context
   - **Start fresh** (new session with the retry prompt) -- cleaner but more expensive

The default behavior is **resume** when the transport supports it (opencode backend does, anthropic backend does, direct backend does via in-memory history). The pipeline TOML does not control this -- it is a transport-level capability.

## Cost Tracking

Each session accumulates costs from `StepFinish` events:

| Field | Source |
|-------|--------|
| `input_tokens` | `StepFinish.input_tokens` |
| `output_tokens` | `StepFinish.output_tokens` |
| `cost_usd` | `StepFinish.cost_usd` |
| `reasoning_tokens` | `StepFinish.reasoning_tokens` |
| `cache_read_tokens` | `StepFinish.cache_read_tokens` |
| `cache_write_tokens` | `StepFinish.cache_write_tokens` |

The pipeline executor sums these across all sessions in a run for the total pipeline cost.

## Session Factory

```python
def create_session(
    transport: Transport,
    model: str,
    system_prompt: str,
    tools: list[Tool],
    session_id: str | None = None,  # for resumption
) -> Session:
    ...
```

- `model` is required -- already resolved from category by the time it reaches session creation
- `system_prompt` is required -- already loaded and substituted from the agent's .md file
- `tools` is required -- already filtered by permissions
- `session_id` is optional -- pass it to resume an existing session

## What This Module Does NOT Do

- Does not choose models (that is agent/ category resolution)
- Does not filter tools (that is agent/ permissions, applied before session creation)
- Does not manage multiple sessions concurrently (that is pipeline/, which creates sessions as needed)
- Does not implement retry logic (that is pipeline/)
- Does not persist session history to disk (opencode backend handles persistence internally; direct backend is in-memory only)
- Does not enforce cost limits (the caller checks `total_cost_usd` and decides whether to continue)
