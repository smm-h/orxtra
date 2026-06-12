# Transport Providers

Each file implements the `Provider` protocol for one LLM API.

## Files

| File | Contents |
|---|---|
| `_anthropic.py` | `AnthropicProvider`. Implements `build_request` (Anthropic Messages API format), `parse_response` (content blocks), `parse_stream` (SSE), `extract_usage` (token counts + cost calculation). Requires `ANTHROPIC_API_KEY`. |
| `_openai.py` | `OpenAIProvider`. Implements the same four methods for OpenAI-compatible APIs (OpenAI, Azure OpenAI). Handles function calling format and `role: "tool"` result messages. |

Adding a new provider means adding a new file here that implements the `Provider` protocol. No other changes needed -- the transport's tool-call loop is provider-agnostic.
