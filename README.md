# orxt

Autonomous multi-agent AI workflows. Complexity if you need it, simplicity if you don't.

Each module is independently useful for a narrow purpose. Together they compose into a full autonomous agent orchestration system.

## Modules

| Layer | Module | Standalone use case |
|---|---|---|
| Foundation | `transport/` | Typed LLM client: Provider protocol, raw httpx, streaming events, tool-call loop |
| Foundation | `agent/` | Agent definition loader: TOML + composable .md prompts, strict validation |
| Foundation | `tool/` | Tool registry: granular constructors, path enforcement, write safety |
| Foundation | `verify/` | Verification runner: ordered callable chains, structured verdicts |
| Foundation | `trace/` | PG event store: schema owner, state machines, LISTEN/NOTIFY, crash recovery |
| Foundation | `notepad/` | Cross-agent IPC: append-only PG-backed context sharing |
| Orchestration | `session/` | Session lifecycle: token tracking, transcript persistence, resumption |
| Orchestration | `scheduler/` | Workflow executor: dependency graphs, parallel steps, budgets, constraints |
| Intelligence | `overseer/` | Persistent LLM brain: 11 decision protocols, PG memory, session handoff |
| Intelligence | `knowledge-module/` | Semantic enrichment via cognee (experimental, disabled by default) |
| Interface | `services/` | Shared business logic for all frontends |
| Interface | `cli/` | strictcli CLI (agents are the primary users) |
| Interface | `mcp/` | MCP server (human interface via dashboard/AI client) |

## Pick what you need

A typed LLM client with streaming and tools:
```
pip install ./transport
```

Deterministic workflow execution (no AI brain, consumer provides workflows):
```
pip install ./scheduler
```

Full autonomous system with Overseer, verification, and human inbox:
```
pip install ./cli
```

## Design principles

- **Each module is independently useful.** Foundation modules have zero dependencies. Higher layers depend on lower layers via concrete types. No downward dependencies. Overseer and scheduler share protocols, not imports.
- **Complexity if you need it, simplicity if you don't.** The Overseer, verification, notepad, and knowledge enrichment are all opt-in. A bare scheduler + transport is a valid system.
- **No bash tool.** Granular tools (read, write, edit, git, exec, http) with typed parameters and path enforcement.
- **PostgreSQL backbone.** All state in PG. Append-only immutable tables. LISTEN/NOTIFY. Advisory locks.
- **Structured decisions, not free-form.** The Overseer picks from menus. If nothing matches, it escalates to the human.
- **No implicit defaults.** Provider, model, database, timeout -- all must be explicit.

See each module's `DESIGN.md` for the full spec.

## Status

Design phase. Implementation has not started.
