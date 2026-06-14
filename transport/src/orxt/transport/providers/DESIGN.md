# Transport Providers

Each file implements the `Provider` protocol for one LLM API using raw httpx (no official SDKs).

## Files

| File | Contents |
|---|---|
| `_anthropic.py` | `AnthropicProvider`. Implements `build_request` (Anthropic Messages API format), `parse_response` (content blocks), `parse_stream` (SSE via httpx async streaming), `extract_usage` (token counts). Requires `ANTHROPIC_API_KEY`. Emits `Thinking` events from `content[type="thinking"]` blocks when the model supports extended thinking. |
| `_openai.py` | `OpenAIProvider`. Implements the same four methods for OpenAI-compatible APIs (OpenAI, Azure OpenAI). Handles function calling format and `role: "tool"` result messages. SSE parsing via httpx async streaming. Does not emit `Thinking` events (OpenAI does not expose reasoning content). |

Adding a new provider means adding a new file here that implements the `Provider` protocol. No other changes needed -- the transport's tool-call loop is provider-agnostic.
