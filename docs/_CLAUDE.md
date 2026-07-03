# orxtra

Autonomous multi-agent AI workflows. Complexity if you need it, simplicity if you don't.

## Status

Active implementation. Monorepo with :-: project-count sub-projects across five layers, implemented across 170+ source modules and 210+ test files. Foundation, orchestration, intelligence, and composition layers are functional; production PG integration and end-to-end hardening in progress.
Current version: :-: rlsbl-version.

## Philosophy

Every module is independently useful for a narrow purpose. Together they compose into a full autonomous agent orchestration system. A consumer wanting only a typed LLM client uses `orxtra.transport`. One wanting deterministic workflow execution without an Overseer brain uses `orxtra.scheduler`. The full system composes all :-: project-count.

### Structured Programming for AI Workflows

orxtra applies the structured programming theorem to AI workflows. Unstructured agent orchestration (free-form spawn, no verification boundaries, ad-hoc delegation) is the `goto` of AI workflows. orxtra replaces it with structured control flow:

- **Sequence**: tasks declare dependencies
- **Selection**: pre-checks gate entry, post-checks gate exit, failure branches to parent
- **Iteration**: failed post-checks let the agent retry, escalation after exhaustion
- **Nesting**: tasks contain subtasks, workflows are tasks, runs are tasks

Every piece of work is a task with explicit boundaries (`start_task` / `end_task`), entry conditions (pre-checks), and exit conditions (post-checks). Tasks nest recursively. Failure propagates up the hierarchy. Budget is the natural depth limit.

Foundation modules have zero intra-workspace dependencies and expose stable interfaces. Higher-layer modules depend on lower-layer concrete types. The critical constraint: no downward dependencies, and the Overseer and scheduler never import each other (they share types via the protocols module).

## Monorepo structure

:-: list-tree path="." depth="1"

Each sub-project has: `pyproject.toml`, `src/orxtra/<name>/`, `tests/`.

## Architecture layers

| Layer | Sub-projects | Dependencies |
|---|---|---|
| Foundation | [protocols](protocols/), [secrets](secrets/), [write-safety](write-safety/), [transport](transport/), [agent](agent/), [tool](tool/), [verify](verify/), [trace](trace/), [notepad](notepad/), [session](session/), [compose](compose/), [auth](auth/), [a2ui](a2ui/), [worker](worker/) | Zero intra-workspace deps (exceptions: transport -> protocols, tool -> protocols + secrets + write-safety, verify -> protocols, notepad -> trace, session -> protocols + transport + trace, agent -> compose, auth -> protocols, a2ui -> protocols, worker -> protocols + tool + write-safety + auth + secrets) |
| Orchestration | [scheduler](scheduler/), [dispatch](dispatch/) | scheduler depends on foundation + dispatch; dispatch depends on protocols |
| Intelligence | [overseer](overseer/) | Depends on foundation (not orchestration -- shared protocols at the seam) |
| Composition | [services](services/) | Depends on orchestration + intelligence; provides concrete implementations (ActionExecutor, FlushScheduler) and service functions |
| Interfaces | [cli](cli/), [mcp](mcp/), [a2a](a2a/), [agui](agui/), [api](api/), [incoming](incoming/) | Depends on composition |

Higher layers can depend on lower layers. Lower layers cannot depend on higher layers. The Overseer and scheduler share types via the protocols module but never import each other.

## Key concepts

- **[Write-safety](write-safety/)** owns the write queue, stale-write detection, atomic replace, and transient replay. Used by tool (enforcement) and scheduler (lifecycle).
- **[Secrets](secrets/)** owns the secret registry, substitution (`{{secret:NAME}}` -> real values in tool args), and scrubbing (real values -> placeholders in results and trace).
- **[Protocols](protocols/)** defines shared types and behavioral contracts. Types: Tool, ToolOutput, result types (FileContent, GrepResult, etc.), TaskSpec, TaskState, Execution variants, event dataclasses, Action types (ScriptAction, LogAction, WorkflowAction, EventAction), dispatch types (Subscription, FilterPredicate, Source). Contracts: EventDelivery, StorageBackend, OverseerProtocol, DispatchBackend, ActionExecutor, FlushScheduler, EventBus, SessionProtocol, Renderer. Also provides `run_sync()` for event-loop-aware sync-to-async bridging.
- **[Transport](transport/)** is a standalone typed LLM client. Provider protocol, raw httpx, streaming events, tool-call loop, auto-retry.
- **[Agent](agent/)** is a standalone TOML+md agent definition loader. Strict validation, prompt composition, category resolution.
- **[Tool](tool/)** is a standalone tool registry. Granular constructors (read, write, edit, git, exec, http, etc.), path enforcement, write safety, task lifecycle tools (start_task, end_task, create_task, create_workflow). No bash tool. Git mutations wrap [safegit](https://github.com/smm-h/safegit); file deletion wraps [saferm](https://github.com/smm-h/saferm).
- **[Verify](verify/)** is the check runner. Runs pre-checks and post-checks for tasks. Checks are Executions: scripts (Python callables), agents (read-only, structured verdicts), or workflows (recursive task trees).
- **[Trace](trace/)** is a standalone PG event store. Schema owner for event-store tables (events, runs, tasks, transcripts, decisions, constraints, etc.). State machines, LISTEN/NOTIFY, append-only tables, crash recovery. Provides PgBackend and InMemoryBackend implementing the StorageBackend protocol. Events support nullable run_id and a source column for external event ingestion.
- **[Notepad](notepad/)** is PG-backed append-only cross-agent IPC.
- **[Session](session/)** wraps transport with token tracking, transcript persistence, cross-restart resumption.
- **[Compose](compose/)** is the fragment-based prompt composition engine. Strict variable substitution, include resolution, priority ordering. Zero intra-workspace deps; defines the FragmentProvider protocol for trace-backed providers above.
- **[Auth](auth/)** is the authentication and authorization module. Consumer registry, credential hashing, ASGI middleware, Authenticator/Authorizer protocols.
- **[A2UI](a2ui/)** is the agent-to-UI surface rendering engine. Template registry, fragment library, data-bound component engine.
- **[Worker](worker/)** is the brain-worker protocol for remote tool execution over WebSocket. Native and Docker workers, pipeline splitting for remote tool calls.
- **[Scheduler](scheduler/)** is the task executor. Manages the recursive task hierarchy, enforces pre/post-checks, handles runtime task creation, routes events to the Overseer, enforces budgets and constraints. Accepts an EventDelivery implementation (defaults to dispatch's TransientEventDelivery) for wait_for task waking. Control signals (pause/abort) flow through trace's subscribe_run_control, not through dispatch.
- **[Dispatch](dispatch/)** is the event delivery engine. Subscriptions with filter predicates, per-subscription action chains, accumulator buffering with count/time thresholds, dual-phase delivery (transient futures + persistent subscriptions). ActionExecutor protocol for injecting workflow execution without downward dependencies.
- **[Overseer](overseer/)** is a persistent LLM with action tools (create_workflow, add_constraint, etc.), PG memory, health monitoring, session handoff. The root task's agent.
- **[Services](services/)** is the composition layer: shared business logic consumed by CLI, MCP, and the Python API. Provides concrete implementations of dispatch protocols (ServicesActionExecutor, AsyncioFlushScheduler) and thin service functions for subscriptions, events, runs, inbox, and trace queries.
- **[CLI](cli/)** is a [strictcli](https://github.com/smm-h/strictcli) frontend. Agents are the primary users.
- **[MCP](mcp/)** is an MCP server for human interface via dashboard/AI client.
- **[A2A](a2a/)** is an A2A (Agent-to-Agent) protocol server. Agent card generation, skill registry, task state bridging.
- **[AG-UI](agui/)** is the AG-UI streaming protocol for human frontends. Event translation, SSE server, state snapshots.
- **[API](api/)** is the HTTP compositor that mounts MCP, A2A, AG-UI, and native routes on a single ASGI app.
- **[Incoming](incoming/)** is the external event ingestion interface. Webhook receiver (POST /events/{slug} with HMAC verification), cursor-based event replay, and SSE stream with hand-built catch-up. Mounted by the api compositor.

## Examples

- [`basic_agent.toml`](examples/basic_agent.toml) -- minimal agent definition
- [`simple_workflow.toml`](examples/simple_workflow.toml) -- workflow with dependencies and post-checks
- [`categories.toml`](examples/categories.toml) -- multi-provider model routing
- [`coder_agent.toml`](examples/coder_agent.toml) -- agent with exec capabilities

## Tooling

### Third-party dependencies

- **pydantic v2** with `strict=True, extra='forbid'` for all schema validation.
- **mypy --strict** with the pydantic plugin.
- **ruff** with `select = ["ALL"]` and documented ignores.
- **httpx** for all LLM API communication (no official SDKs).
- **asyncpg** for PostgreSQL.

### Our tools

The following are all projects under `~/Projects/`, maintained by us. Any feature gap, bug, or shortcoming identified during orxtra development can be filed as a todo in the respective project's `todo/` directory and will be addressed -- these are not external dependencies we're stuck with, they're internal tools that evolve with our needs.

- **[safegit](https://github.com/smm-h/safegit)** (`~/Projects/safegit`) -- concurrency-safe git operations. The git tool's mutation subcommands wrap safegit, not raw git.
- **[saferm](https://github.com/smm-h/saferm)** (`~/Projects/saferm`) -- audited file deletion with mandatory descriptions, audit trail, and recovery. The delete tool wraps saferm, not raw rm.
- **[rlsbl](https://github.com/smm-h/rlsbl)** (`~/Projects/rlsbl`) -- release orchestration, changelog enforcement, CI scaffolding, monorepo workspace management.
- **[strictcli](https://github.com/smm-h/strictcli)** (`~/Projects/strictcli`) -- schema-driven CLI framework. No implicit flags.
- **[pgdesign](https://github.com/smm-h/pgdesign)** (`~/Projects/pgdesign`) -- PostgreSQL schema compiler. Owns `schema/trace.toml` and `schema/dispatch.toml`.
- **[selfdoc](https://github.com/smm-h/selfdoc)** (`~/Projects/selfdoc`) -- documentation generation from templates.


## Conventions

- Use `uv` for dependency management, never pip.
- All prompt text lives in .md files, never in Python strings.
- Variable substitution is strict both ways.
- No implicit defaults for provider, model, database URL, timeout, or retry behavior.
- No silent degradation. If something is configured, it must work. No fallback to alternative strategies at runtime.
- Each module owns its own PG schema. Trace owns event-store tables (events, runs, tasks, etc.). Dispatch owns subscription tables (sources, subscriptions, subscription_actions, accumulator_buffer). [pgdesign](https://github.com/smm-h/pgdesign) manages both via `schema/trace.toml` and `schema/dispatch.toml`.
- Budgets denominated in USD with orxtra-maintained internal pricing table.
- No bash tool. Granular purpose-built tools with typed parameters.
- Git mutations wrap [safegit](https://github.com/smm-h/safegit); file deletion wraps [saferm](https://github.com/smm-h/saferm). Agents cannot bypass these -- there is no raw git or rm.
- Write safety: atomic replace, per-path write queue, transient-only replay, stale-write detection.
- No truncation. Tool output always persisted in full; large results return a preview with opt-in full retrieval.
- No downward dependencies between layers. Overseer and scheduler share types via protocols, not imports.
- All tool calls require an active task. Hard error outside task boundaries.
- Correctness over convenience. Prefer the most correct solution regardless of effort. Never defer work. Never recommend the easy path when a more correct one exists.
- Structural observations are advisory. The scheduler detects improvements but never silently mutates the task tree. It advises; the agent decides.
