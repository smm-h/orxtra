# Transport Module Design

## Responsibility

Send messages to LLMs. Stream responses back as typed events. Handle the tool-call loop (LLM requests tool -> execute tool -> send result -> LLM continues). Auto-retry transient API errors with an explicit policy. Nothing more.

## Event Types

Typed dataclasses for each event in the stream:

| Event | Fields | Meaning |
|---|---|---|
| `StepStart` | `session_id` | A new agent turn begins |
| `Text` | `text` | Text output from the model |
| `StreamDelta` | `text` | Partial token from the SSE stream. Opt-in: only emitted when `stream_deltas=True`. |
| `Thinking` | `text` | Extended thinking block. Provider-specific. |
| `ToolUse` | `tool_name, input, output, status, error, duration_ms` | A tool was called. |
| `StepFinish` | `reason, input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens` | Turn complete. |
| `ApiRetry` | `attempt, max_retries, delay_ms, status_code, error` | A transient API error was retried. |
| `Error` | `name, message, metadata` | Something went wrong. |
| `Result` | `text, session_id, total_input_tokens, total_output_tokens, tool_calls` | Summary of the full invocation. |

These are frozen dataclasses. They are the public API of the transport layer.

## Streaming Behavior

`Transport.send()` returns an `AsyncIterator[Event]` that yields events from **all iterations** of the tool-call loop. When the LLM requests tool calls and the loop continues with another API call, the iterator continues yielding events from the next call. The consumer sees the entire multi-turn exchange as one continuous stream:

`StepStart` -> `Text`/`ToolUse`/`Thinking` events (first API call) -> more `Text`/`ToolUse` events (subsequent calls) -> `StepFinish` (after final text response) -> `Result` (terminal).

## Provider Protocol

```python
class Provider(Protocol):
    def build_request(self, messages, tools, system, model) -> dict: ...
    def parse_response(self, response: dict) -> list[ContentBlock]: ...
    async def parse_stream(self, stream: AsyncIterator[bytes]) -> AsyncIterator[Event]: ...
    def extract_usage(self, response: dict) -> Usage: ...
```

Structural typing. Any object implementing these four methods is a valid provider.

## Tool-Call Loop

1. Build the API request via `provider.build_request()`
2. Send via httpx
3. Parse response into content blocks
4. If `tool_use` blocks: validate args, call `tool.execute(args)`, record `duration_ms`, append results, go to 1
5. If only `text` blocks: turn complete. Emit `Result`.

The loop is the same for all providers. The provider only handles serialization.

## Transient API Error Handling

Auto-retries transient HTTP errors (429, 500, 502, 503). The retry policy is a **required constructor parameter**:

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int
    backoff_base_seconds: float
    backoff_max_seconds: float
    jitter: bool
```

Non-transient errors (400, 401, 403, 404) propagate immediately as `Error` events.

## Providers

All providers use raw httpx -- no official SDKs.

### AnthropicProvider

Calls the Anthropic Messages API directly. Requires `ANTHROPIC_API_KEY`. Emits `Thinking` events from `content[type="thinking"]` blocks.

### OpenAIProvider

Calls OpenAI-compatible APIs. Does not emit `Thinking` events.

## Provider Selection

- `provider` is a required parameter on transport construction. No default.
- Model strings in `categories.toml` use `"provider/model"` format. The transport parses the provider prefix to validate the model matches.
- The scheduler maintains a transport registry (`dict[str, Transport]`) for multi-provider support.

## Session ID Management

- The transport generates UUIDv7 (via the `uuid6` package) for each new session
- Session IDs returned in every `Result` event
- Passing `session_id` to a subsequent `send()` continues the conversation

## Transport Interface

```python
class Transport:
    def __init__(self, provider: Provider, retry_policy: RetryPolicy): ...

    async def send(
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        ...
```

## Files

| File | Contents |
|---|---|
| `_events.py` | Frozen dataclasses for all event types. `ContentBlock`, `Usage`. |
| `_provider.py` | `Provider` protocol definition. `RetryPolicy` frozen dataclass. |
| `_transport.py` | `Transport` class. Wraps a provider, runs the tool-call loop, manages in-memory conversation history for multi-turn exchanges, applies retry policy. |
| `providers/` | Provider implementations. See `providers/DESIGN.md`. |

## What This Module Does NOT Do

- Does not decide which model to use (that is category resolution in the agent module)
- Does not filter tools by permissions (done before tools reach transport)
- Does not track costs in USD (computed at reporting time)
- Does not manage multiple concurrent transports (that is the scheduler)
- Does not implement fallback between providers
- Does not persist transcripts (that is the session module via trace)
