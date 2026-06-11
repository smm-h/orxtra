# Transport Module Design

## Background

oxtra's transport layer is the dissolved remains of a project called "openstream," a Python SDK for the OpenCode JSON streaming protocol. openstream supported two backends: spawning an `opencode` CLI subprocess, and calling Azure OpenAI directly. oxtra absorbs this code and makes it the built-in LLM communication layer.

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

## Transport Interface

```python
class Transport(Protocol):
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

This is a protocol (structural typing), not an abstract base class. Any object with a matching `send` method is a valid transport.

## Backends

Three backends, chosen explicitly (no fallback):

1. **OpenCodeTransport** -- Spawns the `opencode` CLI as a subprocess. Pipes the message in, parses NDJSON events from stdout. Tool execution is handled by opencode itself.
   - Requires: `opencode` binary on PATH (or explicit path)
   - Session resumption: via `--session` and `--continue` flags

2. **DirectTransport** -- Calls an LLM API directly (currently Azure OpenAI). Handles the tool-call loop internally: LLM requests tool -> transport calls `execute()` on the Tool object -> sends result back -> repeats.
   - Requires: API key, resource/endpoint
   - Session resumption: in-memory conversation history (keyed by session_id)

3. **AnthropicTransport** -- Calls the Anthropic Messages API directly. Handles the tool-use loop internally (same pattern as DirectTransport): LLM requests tool -> transport calls `execute()` on the Tool object -> sends `tool_result` back -> repeats until the model stops requesting tools.
   - Requires: `ANTHROPIC_API_KEY` environment variable (or explicit parameter). Missing key is a hard error.
   - API endpoint: `https://api.anthropic.com/v1/messages`
   - Supports all Claude models (haiku, sonnet, opus)
   - Event mapping:
     - `StepStart` -> emitted when the API call begins
     - `Text` -> from `content[type="text"]` blocks, streamed via SSE
     - `ToolUse` -> from `content[type="tool_use"]` blocks. Transport executes the tool, sends `tool_result` back, continues the loop.
     - `StepFinish` -> from `usage` field in the response (input_tokens, output_tokens). Cost calculated from model pricing.
     - `Error` -> from API error responses (4xx, 5xx)
     - `Result` -> aggregated after the full tool-use loop completes
   - Session resumption: in-memory conversation history keyed by session_id (same approach as DirectTransport)
   - Streaming: uses Anthropic's SSE streaming endpoint for real-time Text events
   - System prompt: passed as the `system` parameter (not as a user message)
   - Tools: converted from oxtra Tool objects to Anthropic's tool format (`name`, `description`, `input_schema`)

## Backend Selection

- `backend` is a required parameter on transport construction. No default.
- If using `"opencode"`, the `opencode` binary must be available. Missing binary is a hard error.
- If using `"direct"`, API credentials must be provided. Missing credentials is a hard error.
- If using `"anthropic"`, `ANTHROPIC_API_KEY` must be set (or passed explicitly). Missing key is a hard error.
- No auto-detection, no "try opencode first, fall back to direct."

## Session ID Management

- The transport captures `session_id` from the first `StepStart` event (for opencode backend) or generates a UUID (for direct and anthropic backends)
- Session IDs are returned in every `Result` event
- Passing `session_id` to a subsequent `send()` call continues the conversation
- For opencode: passes `--session <id> --continue` to the CLI
- For direct and anthropic: looks up in-memory conversation history by session_id

## What This Module Does NOT Do

- Does not decide which model to use (that's category resolution in agent/)
- Does not filter tools by permissions (that's done before tools reach transport)
- Does not track costs across sessions (that's session/)
- Does not retry on API errors (that's pipeline/, which decides retry policy)
- Does not manage multiple concurrent transports (that's pipeline/)
- Does not implement fallback between backends
