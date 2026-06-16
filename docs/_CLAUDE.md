# orxt

Autonomous multi-agent AI workflows. Complexity if you need it, simplicity if you don't.

## Status

Design phase, hardened. Monorepo with 16 sub-projects, each independently useful. Each sub-project has a DESIGN.md serving as its implementation spec. Implementation has not started.

## Philosophy

Every module is independently useful for a narrow purpose. Together they compose into a full autonomous agent orchestration system. A consumer wanting only a typed LLM client uses `orxt.transport`. One wanting deterministic workflow execution without an Overseer brain uses `orxt.scheduler`. The full system composes all 16.

### Structured Programming for AI Workflows

orxt applies the structured programming theorem to AI workflows. Unstructured agent orchestration (free-form spawn, no verification boundaries, ad-hoc delegation) is the `goto` of AI workflows. orxt replaces it with structured control flow:

- **Sequence**: tasks declare dependencies
- **Selection**: pre-checks gate entry, post-checks gate exit, failure branches to parent
- **Iteration**: failed post-checks let the agent retry, escalation after exhaustion
- **Nesting**: tasks contain subtasks, workflows are tasks, runs are tasks

Every piece of work is a task with explicit boundaries (`start_task` / `end_task`), entry conditions (pre-checks), and exit conditions (post-checks). Tasks nest recursively. Failure propagates up the hierarchy. Budget is the natural depth limit.

Foundation modules have zero intra-workspace dependencies and expose stable interfaces. Higher-layer modules depend on lower-layer concrete types. The critical constraint: no downward dependencies, and the Overseer and scheduler never import each other (they share types via the protocols module).

## Monorepo structure

```
orxt/
    .rlsbl-monorepo/           # Monorepo workspace config
        workspace.toml
    schema/                     # pgdesign database schema
    knowledge/                  # Consumer domain knowledge (.md and .toml)
    docs/                       # selfdoc templates
    todo/
    scripts/

    protocols/                  # Foundation: shared types and interfaces
    secrets/                    # Foundation: secret registry + scrubbing
    write-safety/               # Foundation: write queue + stale detection
    transport/                  # Foundation: typed LLM client
    agent/                      # Foundation: TOML+md agent loader
    tool/                       # Foundation: tool registry + constructors
    verify/                     # Foundation: check runner (pre/post-check execution)
    trace/                      # Foundation: PG schema owner
    notepad/                    # Foundation: cross-agent IPC
    session/                    # Foundation: session lifecycle

    scheduler/                  # Orchestration: task executor

    overseer/                   # Intelligence: persistent LLM brain
    knowledge-module/           # Intelligence: cognee enrichment (experimental)

    services/                   # Interface: shared business logic
    cli/                        # Interface: strictcli CLI
    mcp/                        # Interface: MCP server
```

Each sub-project has: `pyproject.toml`, `DESIGN.md`, `src/orxt/<name>/`, `tests/`.

## Architecture layers

| Layer | Sub-projects | Dependencies |
|---|---|---|
| Foundation | protocols, secrets, write-safety, transport, agent, tool, verify, trace, notepad, session | Zero intra-workspace deps (exceptions: notepad -> trace, session -> transport + trace, transport -> protocols, tool -> protocols + secrets + write-safety, trace -> secrets, verify -> protocols) |
| Orchestration | scheduler | Depends on foundation |
| Intelligence | overseer, knowledge-module | Depends on foundation (not orchestration -- shared protocols at the seam) |
| Interfaces | services, cli, mcp | Depends on orchestration + intelligence |

Higher layers can depend on lower layers. Lower layers cannot depend on higher layers. The Overseer and scheduler share types via the protocols module but never import each other.

## Key concepts

- **Write-safety** owns the write queue, stale-write detection, atomic replace, and transient replay. Used by tool (enforcement) and scheduler (lifecycle). See `write-safety/DESIGN.md`.
- **Secrets** owns the secret registry, substitution (`{{secret:NAME}}` -> real values in tool args), and scrubbing (real values -> placeholders in results and trace). See `secrets/DESIGN.md`.
- **Protocols** defines shared types: Execution (script/agent/workflow), task lifecycle, event descriptors, action tool schemas, check results. See `protocols/DESIGN.md`.
- **Transport** is a standalone typed LLM client. Provider protocol, raw httpx, streaming events, tool-call loop, auto-retry. See `transport/DESIGN.md`.
- **Agent** is a standalone TOML+md agent definition loader. Strict validation, prompt composition, category resolution. See `agent/DESIGN.md`.
- **Tool** is a standalone tool registry. Granular constructors (read, write, edit, git, exec, http, etc.), path enforcement, write safety, task lifecycle tools (start_task, end_task, create_task, create_workflow). No bash tool. Git mutations wrap safegit; file deletion wraps saferm. See `tool/DESIGN.md`.
- **Verify** is the check runner. Runs pre-checks and post-checks for tasks. Checks are Executions: scripts (Python callables), agents (read-only, structured verdicts), or workflows (recursive task trees). See `verify/DESIGN.md`.
- **Trace** is a standalone PG event store. Schema owner for all persistent state. State machines, LISTEN/NOTIFY, append-only tables, crash recovery. See `trace/DESIGN.md`.
- **Notepad** is PG-backed append-only cross-agent IPC. See `notepad/DESIGN.md`.
- **Session** wraps transport with token tracking, transcript persistence, cross-restart resumption. See `session/DESIGN.md`.
- **Scheduler** is the task executor. Manages the recursive task hierarchy, enforces pre/post-checks, handles runtime task creation, routes events to the Overseer, enforces budgets and constraints. See `scheduler/DESIGN.md`.
- **Overseer** is a persistent LLM with action tools (create_workflow, add_constraint, etc.), PG memory, health monitoring, session handoff. The root task's agent. See `overseer/DESIGN.md`.
- **Knowledge-module** is an experimental cognee enrichment layer over the flat lessons table. Disabled by default. May be removed. See `knowledge-module/DESIGN.md`.
- **Services** is shared business logic consumed by CLI, MCP, and the Python API. See `services/DESIGN.md`.
- **CLI** is a strictcli frontend. Agents are the primary users. See `cli/DESIGN.md`.
- **MCP** is an MCP server for human interface via dashboard/AI client. See `mcp/DESIGN.md`.

## Tooling

### Third-party dependencies

- **pydantic v2** with `strict=True, extra='forbid'` for all schema validation.
- **mypy --strict** with the pydantic plugin.
- **ruff** with `select = ["ALL"]` and documented ignores.
- **httpx** for all LLM API communication (no official SDKs).
- **asyncpg** for PostgreSQL.
- **cognee** (experimental, third-party) for semantic enrichment of the lessons table.

### Our tools

The following are all projects under `~/Projects/`, maintained by us. Any feature gap, bug, or shortcoming identified during orxt development can be filed as a todo in the respective project's `todo/` directory and will be addressed -- these are not external dependencies we're stuck with, they're internal tools that evolve with our needs.

- **safegit** (`~/Projects/safegit`) -- concurrency-safe git operations. The git tool's mutation subcommands wrap safegit, not raw git.
- **saferm** (`~/Projects/saferm`) -- audited file deletion with mandatory descriptions, audit trail, and recovery. The delete tool wraps saferm, not raw rm.
- **rlsbl** (`~/Projects/rlsbl`) -- release orchestration, changelog enforcement, CI scaffolding, monorepo workspace management.
- **strictcli** (`~/Projects/strictcli`) -- schema-driven CLI framework. No implicit flags.
- **pgdesign** (`~/Projects/pgdesign`) -- PostgreSQL schema compiler. Owns `schema/orxt.toml`.
- **selfdoc** (`~/Projects/selfdoc`) -- documentation generation from templates.

### Consumers

- **ark** (`~/Projects/ark`) -- cross-project orchestrator. Runs domain-specific orxt instances across the project ecosystem, manages releases, files todos, coordinates multi-project workflows. orxt is single-project; ark is the meta-orchestrator.

## Conventions

- Use `uv` for dependency management, never pip.
- All prompt text lives in .md files, never in Python strings.
- Variable substitution is strict both ways.
- No implicit defaults for provider, model, database URL, timeout, or retry behavior.
- No silent degradation. If something is configured, it must work. No fallback to alternative strategies at runtime.
- The trace module is the single owner of the PostgreSQL schema.
- Budgets denominated in USD with orxt-maintained internal pricing table.
- No bash tool. Granular purpose-built tools with typed parameters.
- Git mutations wrap safegit; file deletion wraps saferm. Agents cannot bypass these -- there is no raw git or rm.
- Write safety: atomic replace, per-path write queue, transient-only replay, stale-write detection.
- No truncation. Tool output always persisted in full; large results return a preview with opt-in full retrieval.
- No downward dependencies between layers. Overseer and scheduler share types via protocols, not imports.
- All tool calls require an active task. Hard error outside task boundaries.
- Correctness over convenience. Prefer the most correct solution regardless of effort. Never defer work. Never recommend the easy path when a more correct one exists.
- Structural observations are advisory. The scheduler detects improvements but never silently mutates the task tree. It advises; the agent decides.
- orxt is single-project. Cross-project coordination is ark's domain.
