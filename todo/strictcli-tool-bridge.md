# strictcli-orxtra tool bridge

strictcli v0.21.0 shipped `as_tools()`, `Tool` dataclass, `serve_mcp()`, and programmatic invocation (`acall`). The vision from strictcli's `orxt-tool-integration.md` todo — one command definition, two interfaces — is now technically possible.

## Current incompatibilities

- Execute signature: strictcli `async (**kwargs) -> object` vs orxtra `async (dict) -> ToolOutput[Any]`
- Return type: raw object vs ToolOutput[T] with data+text
- orxtra has suspending flag, pipeline wrapping (secrets, tracing), per-session dependency binding
- strictcli has no namespace/tag metadata on tools (but command groups are natural namespaces)

## 10 solutions were analyzed (session June 22-24 2026)

Ranging from thin adapter (20 lines, ToolOutput[Any]) to unified CommandDef layer (shared package, end-to-end type safety). Solution 10 (unified CommandDef) was identified as most correct: a shared `command-protocol` package defining `CommandDef[Params, Result]` with Pydantic model, renderer, tags, namespace, suspending flag. Both projects consume it to emit CLI commands and orxtra tools from one definition.

## Key design decisions already made

- MCP is not the bridge — it's a free consequence. We want direct, in-process, typed invocation.
- strictcli is ours so we can change whatever we want on both sides.
- Hierarchical namespaces matching strictcli command structure (decided for tool graph).
- Capability tags (#readonly, #mutation) — todo filed in strictcli.

## When to build

When a consumer actually needs strictcli commands as agent tools. The tool graph MVP (namespaces, tags, load_tools) should land first since it provides the metadata infrastructure the bridge needs.
