# Transport: Gateway Provider Support

## Context

An e-commerce pipeline project needs to use orxt's transport layer to call LLMs through a custom AI Gateway (a Go Lambda). The gateway has a custom protocol that differs from both OpenAI and Anthropic:

- Request format: `input: [{"type": "text", "text": "..."}]` (not OpenAI `messages`)
- System prompt: top-level `system: "..."` field
- Response: flat object with `content`, `finish_reason`, `usage` (no `choices[]` wrapper)
- Model routing: `model` + optional `provider` field
- Tool calling: currently not supported by the gateway

The target project previously used a custom `DirectClient` that spoke a hardcoded protocol. They want to migrate to orxt's transport layer, which has a clean `Provider` abstraction (OpenAI, Anthropic). A third provider type (gateway) would let them use orxt directly.

## What's needed

1. A `GatewayProvider` in orxt's transport module that speaks this custom protocol
2. `build_request`: converts messages + tools to the gateway's `input` array format
3. `parse_response`: converts the gateway's flat response to orxt's `ContentBlock` list
4. `parse_stream`: if the gateway supports streaming (to be determined)
5. Authentication: `x-api-key` header (not Bearer token)

## Dependency: gateway tool support must come first

The gateway currently returns "tools are not supported" when tools are passed. This is a deliberate v1 design decision (documented in the gateway's DESIGN.md and CLAUDE.md). The two paths forward have very different effort profiles:

**With gateway tool support (prerequisite):** The gateway becomes a pass-through for tools — accepts tool definitions, forwards to OpenAI/Azure/Gemini, parses tool_calls from responses, returns them. The tool-calling loop stays client-side (orxt's Transport). The GatewayProvider is then just format translation: ~100-150 lines, same scope as the existing OpenAI and Anthropic providers. Clean and reliable.

**Without gateway tool support (workaround):** The GatewayProvider would need to implement prompted tool calling — embed tool schemas in the system prompt as text, tell the LLM to output tool calls in a structured text format (XML/JSON blocks), parse them from raw text. This is fundamentally less reliable (LLMs can format things wrong, emit partial JSON, mix tools with text), much harder to implement robustly, and a fragile workaround for a missing infrastructure capability.

These are not interchangeable. Gateway tool support is the foundational fix that makes the client-side work trivial. Without it, the client-side work is a hack. The gateway change is ~2-3 weeks of infra work but benefits every consumer.

## Effort estimate

With gateway tool support: small (~100-150 lines for a new provider).
Without gateway tool support: large (prompted tool calling is complex, fragile, and unreliable).
