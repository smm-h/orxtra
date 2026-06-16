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

## Open question

The gateway currently returns "tools are not supported" when tools are passed. If the gateway adds tool support (likely by passing through to OpenAI's tool calling), the provider just needs to format tools in whatever way the gateway expects. If the gateway never adds tool support, the provider would need to implement prompted tool calling (XML/JSON blocks in text).

## Effort estimate

Small — the provider interface is well-defined. ~100-150 lines for a new provider implementation, similar in scope to the existing OpenAI and Anthropic providers.
