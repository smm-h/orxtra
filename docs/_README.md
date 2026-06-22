# orxtra

Autonomous multi-agent AI workflows. Complexity if you need it, simplicity if you don't.

Every piece of work is a task with explicit boundaries, entry conditions, and exit conditions. Tasks nest recursively. Failure propagates up the hierarchy.

## Modules

| Layer | Module | Standalone use case |
|---|---|---|
| Foundation | `protocols/` | Shared types: Execution, task lifecycle, event descriptors |
| Foundation | `secrets/` | Secret registry, substitution, and scrubbing |
| Foundation | `write-safety/` | Write queue, stale-write detection, atomic replace |
| Foundation | `transport/` | Typed LLM client: Provider protocol, raw httpx, streaming events, tool-call loop |
| Foundation | `agent/` | Agent definition loader: TOML + composable .md prompts, strict validation |
| Foundation | `tool/` | Tool registry: granular constructors, path enforcement, write safety |
| Foundation | `verify/` | Check runner: pre/post-check execution (scripts, agents, workflows) |
| Foundation | `trace/` | PG event store: schema owner, state machines, LISTEN/NOTIFY, crash recovery |
| Foundation | `notepad/` | Cross-agent IPC: append-only PG-backed context sharing |
| Foundation | `session/` | Session lifecycle: token tracking, transcript persistence, resumption |
| Orchestration | `scheduler/` | Task executor: recursive task hierarchy, budgets, constraints |
| Intelligence | `overseer/` | Persistent LLM brain: action tools, PG memory, session handoff |
| Interface | `services/` | Shared business logic for all frontends |
| Interface | `cli/` | strictcli CLI (agents are the primary users) |
| Interface | `mcp/` | MCP server (human interface via dashboard/AI client) |

## Pick what you need

A typed LLM client with streaming and tools:
```
pip install orxtra-transport
```

Deterministic task execution (no AI brain, consumer provides task trees):
```
pip install orxtra-scheduler
```

Full autonomous system with Overseer, verification, and human inbox:
```
pip install orxtra-cli
```

## Design principles

- **Structured programming for AI workflows.** Tasks nest recursively with pre/post-checks. No unstructured delegation. No `goto`.
- **Each module is independently useful.** Foundation modules have zero intra-workspace dependencies. Higher layers depend on lower layers via concrete types.
- **No bash tool.** Granular tools (read, write, edit, git, exec, http) with typed parameters and path enforcement.
- **PostgreSQL backbone.** All state in PG. Append-only immutable tables. LISTEN/NOTIFY. Advisory locks.
- **The Overseer acts via tools.** Action tools enforce structure. Every action recorded in trace.
- **No implicit defaults.** Provider, model, database, timeout -- all must be explicit.
- **No silent degradation.** If something is configured, it must work. No fallback to alternative strategies at runtime.

See each module's `DESIGN.md` for the full spec.

## Status

Active implementation.
