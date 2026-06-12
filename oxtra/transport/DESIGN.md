# Transport Module Design

## Responsibility

Send messages to LLMs. Stream responses back as typed events. Handle the tool-call loop (LLM requests tool -> execute tool -> send result -> LLM continues). Nothing more.

## Event Types

Typed dataclasses for each event in the stream:

| Event | Fields | Meaning |
|---|---|---|
| `StepStart` | `session_id` | A new agent turn begins |
| `Text` | `text` | Text output from the model |
| `ToolUse` | `tool_name, input, output, status, error` | A tool was called |
| `StepFinish` | `reason, input_tokens, output_tokens, cost_usd, reasoning_tokens, cache_read_tokens, cache_write_tokens` | Turn complete |
| `Error` | `name, message, metadata` | Something went wrong |
| `Result` | `text, session_id, total_input_tokens, total_output_tokens, total_cost_usd, tool_calls` | Summary of the full invocation |

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
        """Extract token counts and cost from the provider's response."""
        ...
```

This is a protocol (structural typing), not an abstract base class. Any object implementing these four methods is a valid provider. Adding a new LLM provider means implementing these methods, not writing a new transport.

## Tool-Call Loop

The transport runs a provider-agnostic loop:

1. Build the API request via `provider.build_request(messages, tools, system, model)`
2. Send the request to the provider's API endpoint
3. Parse the response via `provider.parse_response(response)` into content blocks
4. If the response contains `tool_use` blocks:
   - For each tool call: validate arguments against the tool's JSON Schema, call `tool.execute(args)`, collect the result
   - Append the tool results to the message history
   - Go to step 1
5. If the response contains only `text` blocks: the turn is complete. Emit `Result`.

The loop is the same for all providers. The provider only handles serialization and deserialization.

## Providers

### AnthropicProvider

Calls the Anthropic Messages API directly.

- Requires: `ANTHROPIC_API_KEY` environment variable (or explicit parameter). Missing key is a hard error.
- API endpoint: `https://api.anthropic.com/v1/messages`
- Supports all Claude models (haiku, sonnet, opus)
- System prompt: passed as the `system` parameter (not as a user message)
- Tools: converted from oxtra Tool objects to Anthropic's tool format (`name`, `description`, `input_schema`)
- Streaming: SSE via Anthropic's streaming endpoint
- Event mapping:
  - `StepStart` -> emitted when the API call begins
  - `Text` -> from `content[type="text"]` blocks
  - `ToolUse` -> from `content[type="tool_use"]` blocks
  - `StepFinish` -> from `usage` field (input_tokens, output_tokens, cost calculated from model pricing)
  - `Error` -> from API error responses (4xx, 5xx)
  - `Result` -> aggregated after the full tool-use loop completes

### OpenAIProvider

Calls OpenAI-compatible APIs (OpenAI, Azure OpenAI, and compatible endpoints).

- Requires: API key and endpoint. Missing credentials is a hard error.
- Tools: converted from oxtra Tool objects to OpenAI's function calling format
- Streaming: SSE via OpenAI's streaming endpoint
- Tool results: sent as `role: "tool"` messages with `tool_call_id`

## Provider Selection and Model Routing

- `provider` is a required parameter on transport construction. No default.
- Missing credentials for the selected provider is a hard error.
- No auto-detection, no fallback between providers.

Model strings in `categories.toml` use the format `"provider/model"` (e.g., `"anthropic/claude-haiku-4-5"`, `"openai/gpt-4o"`). The transport parses the provider prefix to validate that the model matches the configured provider. A model string with a mismatched prefix (e.g., `"openai/gpt-4o"` on an AnthropicProvider) is a hard error. The model name after the prefix is passed to the provider's API as-is.

## Session ID Management

- The transport generates a UUID for each new session
- Session IDs are returned in every `Result` event
- Passing `session_id` to a subsequent `send()` call continues the conversation
- Conversation history is tracked in-memory, keyed by session_id

## Transport Interface

```python
class Transport:
    def __init__(self, provider: Provider): ...

    async def send(
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
    ) -> AsyncIterator[Event]:
        ...
```

The transport wraps a provider and runs the tool-call loop. The caller does not interact with the provider directly.

## What This Module Does NOT Do

- Does not decide which model to use (that's category resolution in agent/)
- Does not filter tools by permissions (that's done before tools reach transport)
- Does not track costs across sessions (that's session/)
- Does not retry on API errors (that's pipeline/, which decides retry policy)
- Does not manage multiple concurrent transports (that's pipeline/)
- Does not implement fallback between providers
