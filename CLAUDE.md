# oxtra

Autonomous multi-agent AI workflows. Complexity if you need it, simplicity if you don't.

## Status

Design phase, hardened. Monorepo with 13 sub-projects, each independently useful. Each sub-project has a DESIGN.md serving as its implementation spec. Implementation has not started.

## Philosophy

Every module is independently useful for a narrow purpose. Together they compose into a full autonomous agent orchestration system. A consumer wanting only a typed LLM client uses `transport/`. One wanting deterministic workflow execution without an Overseer brain uses `scheduler/`. The full system composes all 13.

Foundation modules have zero intra-workspace dependencies and expose stable interfaces. Higher-layer modules depend on lower-layer concrete types. The critical constraint: no downward dependencies, and the Overseer and scheduler never import each other (they share protocols at that seam).

## Monorepo structure

```
oxtra/
    .rlsbl-monorepo/           # Monorepo workspace config
        workspace.toml
    schema/                     # pgdesign database schema
    knowledge/                  # Consumer domain knowledge (.md and .toml)
    docs/                       # selfdoc templates
    todo/
    scripts/

    transport/                  # Foundation: typed LLM client
    agent/                      # Foundation: TOML+md agent loader
    tool/                       # Foundation: tool registry + constructors
    verify/                     # Foundation: verification runner
    trace/                      # Foundation: PG schema owner
    notepad/                    # Foundation: cross-agent IPC

    session/                    # Orchestration: session lifecycle
    scheduler/                  # Orchestration: workflow executor

    overseer/                   # Intelligence: persistent LLM brain
    knowledge-module/           # Intelligence: cognee enrichment (experimental)

    services/                   # Interface: shared business logic
    cli/                        # Interface: strictcli CLI
    mcp/                        # Interface: MCP server
```

Each sub-project has: `pyproject.toml`, `DESIGN.md`, `src/<name>/`, `tests/`.

## Architecture layers

| Layer | Sub-projects | Dependencies |
|---|---|---|
| Foundation | transport, agent, tool, verify, trace, notepad | Zero intra-workspace deps (except notepad -> trace) |
| Orchestration | session, scheduler | Depend on foundation |
| Intelligence | overseer, knowledge-module | Depend on foundation (not orchestration -- protocol boundaries) |
| Interfaces | services, cli, mcp | Depend on orchestration + intelligence |

Higher layers can depend on lower layers. Lower layers cannot depend on higher layers. Enforced by `rlsbl check --tag workspace`.

## Key concepts

- **Transport** is a standalone typed LLM client. Provider protocol, raw httpx, streaming events, tool-call loop, auto-retry. See `transport/DESIGN.md`.
- **Agent** is a standalone TOML+md agent definition loader. Strict validation, prompt composition, category resolution. See `agent/DESIGN.md`.
- **Tool** is a standalone tool registry. Granular constructors (read, write, edit, git, exec, http, etc.), path enforcement, write safety, no-truncation previews. No bash tool. See `tool/DESIGN.md`.
- **Verify** is a standalone verification runner. Ordered callable chains, structured verdicts, severity-gated blocking. See `verify/DESIGN.md`.
- **Trace** is a standalone PG event store. Schema owner for all persistent state. State machines, LISTEN/NOTIFY, append-only tables, crash recovery. See `trace/DESIGN.md`.
- **Notepad** is PG-backed append-only cross-agent IPC. See `notepad/DESIGN.md`.
- **Session** wraps transport with token tracking, transcript persistence, cross-restart resumption. See `session/DESIGN.md`.
- **Scheduler** is a deterministic workflow executor. Dependency graphs, parallel steps, budgets, constraints, verification dispatch. See `scheduler/DESIGN.md`.
- **Overseer** is a persistent LLM with 11 structured decision protocols, PG memory, health monitoring, session handoff. See `overseer/DESIGN.md`.
- **Knowledge-module** is an experimental cognee enrichment layer over the flat lessons table. Disabled by default. See `knowledge-module/DESIGN.md`.
- **Services** is shared business logic consumed by CLI, MCP, and the Python API. See `services/DESIGN.md`.
- **CLI** is a strictcli frontend. Agents are the primary users. See `cli/DESIGN.md`.
- **MCP** is an MCP server for human interface via dashboard/AI client. See `mcp/DESIGN.md`.

## Tooling

- **pydantic v2** with `strict=True, extra='forbid'` for all schema validation.
- **mypy --strict** with the pydantic plugin.
- **ruff** with `select = ["ALL"]` and documented ignores.
- **httpx** for all LLM API communication (no official SDKs).
- **asyncpg** for PostgreSQL.
- **cognee** (experimental) for semantic enrichment of the lessons table.
- **rlsbl** monorepo for release orchestration and changelog enforcement.
- **strictcli** for the CLI.
- **pgdesign** for database schema definition (`schema/oxtra.toml`).

## Conventions

- Use `uv` for dependency management, never pip.
- All prompt text lives in .md files, never in Python strings.
- Variable substitution is strict both ways.
- No implicit defaults for provider, model, database URL, timeout, or retry behavior.
- The trace module is the single owner of the PostgreSQL schema.
- Budgets denominated in USD with oxtra-maintained internal pricing table.
- No bash tool. Granular purpose-built tools with typed parameters.
- Write safety: atomic replace, per-path write queue, transient-only replay, stale-write detection.
- No truncation. Tool output always persisted in full; large results return a preview with opt-in full retrieval.
- No downward dependencies between layers. Overseer and scheduler share protocols, not imports.
