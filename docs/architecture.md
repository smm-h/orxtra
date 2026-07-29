---
title: Architecture
description: Five-layer architecture, module dependency DAG, core concepts, data flow from task submission to completion, and the structured programming model.
---

# Architecture

orxtra is a monorepo of 26 sub-projects organized into five layers. Each module is independently useful for a narrow purpose. Together they compose into a full autonomous agent orchestration system.

A consumer wanting only a typed LLM client uses `orxtra.transport`. One wanting deterministic workflow execution without an Overseer brain uses `orxtra.scheduler`. The full system composes all 26.

## Five-layer architecture

| Layer | Sub-projects | Role |
|---|---|---|
| **Foundation** | protocols, secrets, write-safety, transport, agent, tool, verify, trace, notepad, session, compose, auth, identity, a2ui, worker, notification | Stable interfaces and building blocks. Zero or minimal intra-workspace dependencies. |
| **Orchestration** | scheduler, dispatch | Task execution and event delivery. |
| **Intelligence** | overseer | Persistent LLM brain with action tools and PG memory. |
| **Composition** | services | Shared business logic wiring orchestration and intelligence into concrete implementations. |
| **Interfaces** | cli, mcp, a2a, agui, api, incoming | User-facing frontends for agents, humans, and external systems. |

Higher layers depend on lower layers. Lower layers never depend on higher layers. The Overseer and Scheduler share types via the protocols module but never import each other.

## Module dependency DAG

### Foundation layer

Modules with zero intra-workspace dependencies:

- **protocols** -- shared types and behavioral contracts. Defines Tool, ToolOutput, TaskSpec, TaskState, Execution variants, Action types, dispatch types, auth/identity types, Capability, and protocol contracts (EventDelivery, StorageBackend, OverseerProtocol, DispatchBackend, ActionExecutor, FlushScheduler, EventBus, SessionProtocol, PrincipalStorage, NotificationPort, etc.)
- **write-safety** -- write queue, stale-write detection, atomic replace, transient replay
- **compose** -- fragment-based prompt composition engine. Strict variable substitution, include resolution, priority ordering

Modules with minimal foundation dependencies:

- **secrets** -- secret registry, `{{secret:NAME}}` substitution, scrubbing. Depends on: protocols
- **transport** -- typed LLM client. Provider protocol, raw httpx, streaming events, tool-call loop, auto-retry. Depends on: protocols
- **agent** -- TOML+md agent definition loader. Strict validation, prompt composition, category resolution. Depends on: compose
- **tool** -- tool registry with granular constructors (read, write, edit, git, exec, http, lifecycle). Path enforcement, write safety, secret scrubbing. Depends on: protocols, secrets, write-safety
- **verify** -- check runner for pre-checks and post-checks. Checks are Executions: scripts, agents, or workflows. Depends on: protocols
- **trace** -- PG event store. Schema owner for event-store tables. State machines, LISTEN/NOTIFY, append-only tables, crash recovery. Provides PgBackend and InMemoryBackend. Depends on: protocols
- **notepad** -- PG-backed append-only cross-agent IPC. Depends on: trace
- **session** -- wraps transport with token tracking, transcript persistence, cross-restart resumption. Depends on: protocols, transport, trace
- **auth** -- authentication and authorization. Consumer registry, credential hashing, ASGI middleware. Depends on: protocols
- **identity** -- persisted principals (durable actor identity). KindRegistry, caller resolver, PG and in-memory backends. Depends on: protocols
- **a2ui** -- agent-to-UI surface rendering. Template registry, fragment library, data-bound component engine. Depends on: protocols
- **worker** -- brain-worker protocol for remote tool execution over WebSocket. WorkerRegistry, BrainWorkerBridge, ToolLocation routing. Depends on: protocols, tool, write-safety, auth, secrets
- **notification** -- notification delivery via dispatch subscriptions. PG and in-memory backends, SSE streaming, catch-up replay. Depends on: protocols

### Orchestration layer

- **dispatch** -- event delivery engine. Subscriptions with filter predicates, per-subscription action chains, accumulator buffering with count/time thresholds, dual-phase delivery (transient futures + persistent subscriptions). Depends on: protocols, trace
- **scheduler** -- task executor. Manages the recursive task hierarchy, enforces pre/post-checks, handles runtime task creation, routes events to the Overseer, enforces budgets and constraints. Depends on: protocols, agent, compose, tool, write-safety, session, trace, verify, notepad, secrets, transport, dispatch

### Intelligence layer

- **overseer** -- persistent LLM with action tools (create_workflow, add_constraint, etc.), PG memory, health monitoring, session handoff. The root task's agent. Depends on: protocols, trace, session, tool, transport

The Overseer and Scheduler never import each other. They communicate through protocols: the Scheduler holds an OverseerInterface, and the Overseer receives OverseerEvents from the Scheduler. Shared types (OverseerProtocol, OverseerEvent, TaskSpec, TaskState) live in protocols.

### Composition layer

- **services** -- shared business logic consumed by all frontends. Provides concrete implementations of dispatch protocols (ServicesActionExecutor, AsyncioFlushScheduler), the RunManager, the DispatchContext, and thin service functions for subscriptions, events, runs, inbox, identity, notifications, and trace queries. Depends on: agent, auth, dispatch, identity, notification, overseer, protocols, scheduler, secrets, session, tool, trace, transport

### Interfaces layer

- **cli** -- strictcli frontend. Agents are the primary users. Depends on: api, identity, protocols, services, worker
- **mcp** -- MCP server for human interface via dashboard/AI client. Depends on: protocols, services
- **a2a** -- A2A (Agent-to-Agent) protocol server. Agent card generation, skill registry, task state bridging. Depends on: protocols, services
- **agui** -- AG-UI streaming protocol for human frontends. Event translation, SSE server, state snapshots. Depends on: identity, protocols, services, transport
- **api** -- HTTP compositor that mounts MCP, A2A, AG-UI, incoming, and native routes on a single ASGI app. Depends on: a2a, agui, auth, dispatch, identity, incoming, mcp, notification, protocols, secrets, services, worker
- **incoming** -- webhook receiver, cursor-based event replay, SSE stream. External event ingestion. Depends on: auth, dispatch, protocols, secrets, services, trace

## Core concepts

### Overseer

A persistent LLM session that serves as the root task's agent. The Overseer has read-only tools (read, grep, glob, diff, stat, list_dir, notepad) plus action tools for governance: `create_workflow`, `add_constraint`, `record_decision`, `record_assumption`, `write_lesson`, `update_workflow_status`, `create_inbox_item`. It maintains PG-backed memory (decisions, assumptions, lessons, constraints, workflow status) and monitors system health.

The Overseer does not import the Scheduler. It receives events (task started, task failed, budget threshold crossed, health degraded, escalation, etc.) via the OverseerEvent protocol, and its responses flow back through the OverseerInterface abstraction that the Scheduler holds.

### Scheduler

The task executor. Manages a recursive task hierarchy where tasks contain subtasks, workflows are tasks, and runs are tasks. The Scheduler:

- Builds a dependency graph from workflow definitions and executes tasks in topological order
- Finds parallel groups for concurrent execution
- Enforces pre-checks before task entry and post-checks before task exit
- Handles runtime task creation (agents can create new tasks and workflows during execution)
- Routes events to the Overseer and delivers transport events to subscribed sinks
- Enforces budgets denominated in USD with an internal pricing table
- Manages write safety (per-path write queue, stale-write tracking) and file locks
- Classifies errors into categories (infra, parse, build_env, logic) for intelligent retry
- Accepts an EventDelivery implementation for wait_for task waking

### Agent

A TOML+md agent definition. Agents declare their name, category, model, tools, prompt fragments, and capabilities. The agent module loads TOML definitions and validates them strictly. Prompt composition uses the compose engine for fragment-based assembly with variable substitution and include resolution.

### Worker

Remote tool execution over WebSocket. Workers connect, authenticate (code 4001 on failure), register their capabilities via WorkerRegistration, receive tool call requests, and return results.

ToolLocation routing splits tools into LOCAL (lifecycle tools like start_task, end_task that always execute on the brain) and ANYWHERE (tools that can be routed to a remote worker when the task's `execution_target` is set). The `should_route_to_worker` function combines tool location and task target to decide routing.

### Transport

A standalone typed LLM client. Implements the Provider protocol for multi-provider support, uses raw httpx (no official SDKs), handles streaming with typed events (Text, Thinking, ToolUse, Usage, etc.), manages the tool-call loop, and provides auto-retry with configurable RetryPolicy. A state machine (TransportState with Continuation) tracks session state across multi-turn conversations.

## Data flow: task submission to completion

### 1. Run initialization

A caller (CLI, API, MCP) invokes `services.start_run` with a `RunConfig` specifying the workflow path, agent directory, provider configs, budget, and autonomy level.

The service layer:

1. Generates a UUIDv7 run ID
2. Mints a run principal via PrincipalStorage (mint-first, idempotent -- an orphaned principal from a crash is harmless)
3. Creates the run record in the trace store with the caller as `created_by`
4. Transitions the run to `running` state
5. Loads agents, categories, transport providers, secrets, and custom data-defined tools
6. Builds refresh callbacks for constraints, lessons, and notepad (when a StorageBackend is available)
7. Constructs the Scheduler with all dependencies

### 2. Workflow loading and graph construction

The Scheduler calls `load_workflow` to parse the TOML workflow definition into a WorkflowConfig. It then calls `build_graph` to construct a dependency graph, `topological_sort` to determine execution order, and `find_parallel_groups` to identify tasks that can run concurrently.

Knowledge files are loaded into the trace store for the run.

### 3. Task execution loop

For each task in the workflow (respecting dependency order and parallelism):

1. **Pre-check phase**: The verify module's `run_checks` executes the task's pre-checks. Checks are Executions (scripts, agents, or workflows). If a check fails and provides a fix callback, the fix is applied and the check re-run. A failing pre-check prevents task entry.

2. **Task activation**: The task transitions to ACTIVE state via the trace state machine. A Session is created for the task's agent, wrapping a Transport configured for the agent's provider and model.

3. **Agent execution**: The Session drives the transport's tool-call loop. The agent receives its composed prompt and available tools. When the agent calls a tool:
   - The tool module validates the call against the tool's schema
   - Write tools go through the write-safety pipeline (write queue serialization, stale-write detection, atomic replace)
   - Secret substitution replaces `{{secret:NAME}}` placeholders in tool arguments with real values; scrubbing replaces real values in results with placeholders
   - ToolLocation routing decides whether the call executes locally or on a remote worker
   - All tool calls require an active task (hard error outside task boundaries)
   - Tool output is persisted in full (no truncation); large results return a preview with opt-in full retrieval

4. **Runtime task creation**: Agents can create new tasks and workflows mid-execution via lifecycle tools (`create_task`, `create_workflow`, `start_task`, `end_task`). The Scheduler validates and integrates these into the task tree.

5. **Post-check phase**: The verify module runs post-checks. Failed post-checks allow the agent to retry (iteration). After retry exhaustion, the task escalates to its parent.

6. **Completion or escalation**: A task that passes post-checks transitions to COMPLETED. A task that exhausts retries transitions to ESCALATED. Failure propagates up the recursive task hierarchy.

### 4. Event delivery

Events flow through two parallel systems:

- **Transient delivery** (TransientEventDelivery): In-memory asyncio Futures for wait_for task waking. Events fired before any waiter registers are silently lost (no replay).
- **Persistent delivery** (dispatch subscriptions): Subscriptions with filter predicates and action chains. The DispatchWorker processes the accumulator buffer with count/time thresholds and executes actions (ScriptAction, LogAction, WorkflowAction, EventAction, NotifyAction).

Control signals (pause/abort) flow through the trace module's `subscribe_run_control`, not through dispatch.

### 5. Overseer interaction

Throughout execution, the Scheduler sends OverseerEvents to the Overseer: task started, task failed, budget threshold crossed, health degraded, escalation payloads. The Overseer processes these through its persistent LLM session, potentially responding with governance actions (adding constraints, creating workflows, recording decisions).

### 6. Run completion

When all tasks complete (or the run fails/is aborted), the run transitions to its final state. The RunManager deregisters the Scheduler, ending live SSE subscriptions for AG-UI clients.

## Structured programming model

orxtra applies the structured programming theorem to AI workflows. Unstructured agent orchestration (free-form spawn, no verification boundaries, ad-hoc delegation) is the `goto` of AI workflows. orxtra replaces it with structured control flow.

### Task boundaries

Every piece of work is a task with explicit boundaries:

- `start_task` marks entry
- `end_task` marks exit
- All tool calls require an active task (hard error outside boundaries)
- Tasks nest recursively: tasks contain subtasks, workflows are tasks, runs are tasks

### Pre-checks and post-checks

Pre-checks gate entry. Post-checks gate exit. Both are Executions -- one of three forms:

- **ScriptExecution**: a Python callable that evaluates conditions programmatically
- **AgentExecution**: a read-only agent session that produces structured CheckVerdicts
- **WorkflowExecution**: a recursive task tree for complex verification

Each check produces a CheckResult with a verdict, issues (with Severity levels), and optionally a fix callback. If a check fails and provides a fix, the fix is applied and the check re-run automatically.

### Control flow primitives

| Primitive | Mechanism |
|---|---|
| **Sequence** | Tasks declare dependencies; topological sort determines order |
| **Selection** | Pre-checks gate entry, post-checks gate exit, failure branches to parent |
| **Iteration** | Failed post-checks allow the agent to retry; escalation after exhaustion |
| **Nesting** | Tasks contain subtasks, workflows are tasks, runs are tasks |

### Write safety

The write-safety module prevents data corruption from concurrent or stale writes:

- **WriteQueue**: per-path serialization ensures only one write to a given file at a time
- **StaleWriteTracker**: content hashing detects when an agent's read is stale (another agent modified the file since it was read)
- **atomic_write**: writes go to a temporary file first, then atomically rename to the target path
- **with_transient_retry**: retries on transient errors (network timeouts, temporary lock contention)

### Secret management

The secrets module handles credential lifecycle:

- **Substitution**: `{{secret:NAME}}` in tool arguments is replaced with the real value before execution
- **Scrubbing**: real values in tool results and trace output are replaced with placeholders before persistence
- No secrets appear in the trace store or agent context

### Authorization enforcement

Every Capability declares a `required_scope` and its `injects` (pool, dispatch_backend, principal_storage, kind_registry, caller_principal, etc.). The single dispatch choke point in the services layer authenticates the caller's AuthContext and verifies it carries the required scope before routing to the service function. An API mounted without an authenticator serves public surfaces only.

Attribution is pervasive: every event carries a NOT NULL `principal_id` naming the actor that emitted it. Principals are durable identity records (one per actor) with RESTRICT foreign keys on history-bearing tables (events, runs, sources, inbox items) -- an actor with history is undeletable.

### Budget enforcement

Budgets are denominated in USD using an orxtra-maintained internal pricing table. The session module tracks token usage per model and computes costs. The Scheduler monitors cumulative spend against the budget limit and fires BudgetThresholdCrossed events to the Overseer. The BudgetExhaustionPolicy determines behavior when the budget is exhausted.
