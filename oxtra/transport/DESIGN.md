# Transport Module Design

## Responsibility

Send messages to LLMs. Stream responses back as typed events. Handle the tool-call loop (LLM requests tool -> execute tool -> send result -> LLM continues). Auto-retry transient API errors with an explicit policy. Nothing more.

## Event Types

Typed dataclasses for each event in the stream:

| Event | Fields | Meaning |
|---|---|---|
| `StepStart` | `session_id` | A new agent turn begins |
| `Text` | `text` | Text output from the model |
| `StreamDelta` | `text` | Partial token from the SSE stream. Opt-in: only emitted when `stream_deltas=True` is passed to `Transport.send()`. |
| `Thinking` | `text` | Extended thinking block from the model. Provider-specific: emitted by AnthropicProvider for models that support extended thinking, not emitted by OpenAIProvider. |
| `ToolUse` | `tool_name, input, output, status, error, duration_ms` | A tool was called. `duration_ms` records wall-clock execution time. |
| `StepFinish` | `reason, input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens` | Turn complete. No cost_usd -- cost is computed at reporting time from the internal pricing table. |
| `ApiRetry` | `attempt, max_retries, delay_ms, status_code, error` | A transient API error was retried by the transport. |
| `Error` | `name, message, metadata` | Something went wrong (after retries exhausted, or non-transient) |
| `Result` | `text, session_id, total_input_tokens, total_output_tokens, tool_calls` | Summary of the full invocation |

These are frozen dataclasses. They are the public API of the transport layer.

## Provider Protocol

```python
class Provider(Protocol):
    def build_request(
        self,
        messages: list[dict],
        tools: list[Tool],
        system: str,
        model: str,
    ) -> dict:
        """Convert oxtra's internal message format to the provider's API request format."""
        ...

    def parse_response(self, response: dict) -> list[ContentBlock]:
        """Convert the provider's API response to oxtra's content blocks (text, tool_use)."""
        ...

    async def parse_stream(self, stream: AsyncIterator[bytes]) -> AsyncIterator[Event]:
        """Parse the provider's SSE stream into typed oxtra events."""
        ...

    def extract_usage(self, response: dict) -> Usage:
        """Extract token counts from the provider's response."""
        ...
```

This is a protocol (structural typing), not an abstract base class. Any object implementing these four methods is a valid provider. Adding a new LLM provider means implementing these methods, not writing a new transport.

## Tool-Call Loop

The transport runs a provider-agnostic loop:

1. Build the API request via `provider.build_request(messages, tools, system, model)`
2. Send the request to the provider's API endpoint via httpx
3. Parse the response via `provider.parse_response(response)` into content blocks
4. If the response contains `tool_use` blocks:
   - For each tool call: validate arguments against the tool's JSON Schema, call `tool.execute(args)`, collect the result, record `duration_ms`
   - Append the tool results to the message history
   - Go to step 1
5. If the response contains only `text` blocks: the turn is complete. Emit `Result`.

The loop is the same for all providers. The provider only handles serialization and deserialization.

## Transient API Error Handling

The transport auto-retries transient HTTP errors (429, 500, 502, 503) internally. The retry policy is a **required constructor parameter** on `Transport` -- no implicit defaults.

```python
@dataclass(frozen=True)
class RetryPolicy:
    max_retries: int           # e.g., 3
    backoff_base_seconds: float  # e.g., 1.0
    backoff_max_seconds: float   # e.g., 60.0
    jitter: bool               # e.g., True
```

On each retry, the transport emits an `ApiRetry` event so the scheduler can observe retry frequency. After retries are exhausted, an `Error` event propagates to the caller. The error is classified as `infra` by the scheduler's error taxonomy.

Non-transient errors (400, 401, 403, 404) are never retried -- they propagate immediately as `Error` events.

## Providers

All providers use **raw httpx** -- no official SDKs. This gives full control of SSE parsing, retry behavior, and event mapping.

### AnthropicProvider

Calls the Anthropic Messages API directly via httpx.

- Requires: `ANTHROPIC_API_KEY` environment variable (or explicit parameter). Missing key is a hard error.
- API endpoint: `https://api.anthropic.com/v1/messages`
- System prompt: passed as the `system` parameter (not as a user message)
- Tools: converted from oxtra Tool objects to Anthropic's tool format (`name`, `description`, `input_schema`)
- Streaming: SSE parsing via httpx async streaming
- Event mapping:
  - `StepStart` -> emitted when the API call begins
  - `Text` -> from `content[type="text"]` blocks
  - `Thinking` -> from `content[type="thinking"]` blocks (models that support extended thinking)
  - `ToolUse` -> from `content[type="tool_use"]` blocks
  - `StepFinish` -> from `usage` field (input_tokens, output_tokens, reasoning tokens)
  - `Error` -> from API error responses (4xx, 5xx)
  - `Result` -> aggregated after the full tool-use loop completes

### OpenAIProvider

Calls OpenAI-compatible APIs (OpenAI, Azure OpenAI, and compatible endpoints) via httpx.

- Requires: API key and endpoint. Missing credentials is a hard error.
- Tools: converted from oxtra Tool objects to OpenAI's function calling format
- Streaming: SSE parsing via httpx async streaming
- Tool results: sent as `role: "tool"` messages with `tool_call_id`
- Does not emit `Thinking` events (OpenAI does not expose reasoning content)

## Provider Selection and Model Routing

- `provider` is a required parameter on transport construction. No default.
- Missing credentials for the selected provider is a hard error.
- No auto-detection, no fallback between providers.

Model strings in `categories.toml` use the format `"provider/model"` (e.g., `"anthropic/claude-haiku-4-5"`, `"openai/gpt-4o"`). The transport parses the provider prefix to validate that the model matches the configured provider. A model string with a mismatched prefix is a hard error. The model name after the prefix is passed to the provider's API as-is.

## Session ID Management

- The transport generates a UUID for each new session
- Session IDs are returned in every `Result` event
- Passing `session_id` to a subsequent `send()` call continues the conversation
- Conversation history is persisted in PostgreSQL via the trace module, enabling cross-restart resumption

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

The transport wraps a provider and runs the tool-call loop. The caller does not interact with the provider directly.

## Files

| File | Contents |
|---|---|
| `_events.py` | Frozen dataclasses for all event types: `StepStart`, `Text`, `StreamDelta`, `Thinking`, `ToolUse`, `StepFinish`, `ApiRetry`, `Error`, `Result`. Also `ContentBlock`, `Usage`. |
| `_provider.py` | `Provider` protocol definition. `RetryPolicy` frozen dataclass. |
| `_transport.py` | `Transport` class. Wraps a provider, runs the tool-call loop, manages conversation history via trace module, applies retry policy. |
| `providers/` | Provider implementations. See `providers/DESIGN.md`. |

## What This Module Does NOT Do

- Does not decide which model to use (that's category resolution in agent/)
- Does not filter tools by permissions (that's done before tools reach transport)
- Does not track costs in USD (that's computed at reporting time from the pricing table)
- Does not manage multiple concurrent transports (that's scheduler/)
- Does not implement fallback between providers
