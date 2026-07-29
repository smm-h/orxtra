---
title: Key Concepts
description: Core concepts unique to orxtra -- structured task boundaries, pre/post-checks, write safety, budget enforcement, action tools, event subscriptions, principal-based attribution, and categories.
---

# Key Concepts

This page explains the concepts that define orxtra's approach to agent orchestration. Each concept is a deliberate design choice that replaces ad-hoc patterns with structured, verifiable alternatives.

## Structured task boundaries

Every piece of work in orxtra is a **task** with explicit entry and exit points. An agent enters a task by calling `start_task` and exits by calling `end_task`. All tool calls between these boundaries belong to that task. A tool call outside task boundaries is a hard error.

Tasks nest recursively. A task can contain subtasks. A workflow is a task. A run is a task. This recursive nesting is the foundation of orxtra's structured control flow -- the same verification and lifecycle rules apply at every level of the hierarchy.

### Lifecycle tools

The task lifecycle tools are:

- `start_task` -- enter a task, triggering its pre-checks
- `end_task` -- complete the active task with a summary message, triggering post-checks
- `create_task` -- create a subtask within the current active task
- `create_workflow` -- create a goal-oriented task tree within the current active task
- `create_wait_for` -- create a task that blocks until a named event fires or a timeout expires
- `await_task` -- suspend the current session until a child task completes

All lifecycle tools are LOCAL -- they always execute on the brain, never on a remote worker.

### Task states

A task transitions through a well-defined state machine:

- `created` -- task exists but has not been entered
- `prechecking` -- pre-checks are running
- `active` -- the agent is working inside the task
- `suspended` -- the agent is waiting for a child task
- `postchecking` -- post-checks are running
- `completed` -- the task passed its post-checks
- `precheck_failed` -- a pre-check failed, preventing entry
- `postcheck_failed` -- a post-check failed after the agent finished
- `escalated` -- the task exhausted its retries and escalated to its parent
- `cancelled` -- the task was cancelled

### Example: workflow definition

```toml
format_version = 1

[workflow]
name = "feature-pipeline"
description = "Implement a feature with tests and lint check."

[[tasks]]
name = "implement"
agent = "coder"
task_prompt = "Implement the feature described in the project spec."
timeout = 600
context_refinement = true

[[tasks]]
name = "test"
agent = "coder"
task_prompt = "Write tests for the implementation."
depends_on = ["implement"]
timeout = 600
context_refinement = true

[tasks.postchecks]
scripts = ["myproject.checks:pytest_passes"]

[[tasks]]
name = "lint"
agent = "coder"
task_prompt = "Fix any lint issues found by ruff."
depends_on = ["implement"]
timeout = 600
context_refinement = true

[tasks.postchecks]
scripts = ["myproject.checks:ruff_passes"]
```

The `depends_on` field declares task dependencies. The scheduler builds a DAG, determines topological order, and identifies parallel groups. In this example, `test` and `lint` both depend on `implement` and can run concurrently once `implement` completes.

## Pre-checks and post-checks

Pre-checks gate entry to a task. Post-checks gate exit. Together they form the **selection** primitive in orxtra's structured control flow: pre-checks decide whether work should begin, and post-checks verify whether work was done correctly.

Both pre-checks and post-checks are **Executions** -- a union type with three variants:

### ScriptExecution

A Python callable that evaluates conditions programmatically. The callable path uses `module:function` format and receives a `CheckContext` with the run ID, task name, agent output (for post-checks), and variable bindings.

```toml
[tasks.postchecks]
scripts = ["myproject.checks:pytest_passes"]
```

### AgentExecution

A read-only agent session that produces a structured `CheckVerdict`. The agent reviews the work product (agent output, mechanical check results, notepad content) and issues a verdict with individual `CheckIssue` entries. Each issue has a `Severity` (critical, major, minor, nit), and the `block_threshold` on the execution determines which severities are blocking.

```toml
[tasks.postchecks.agents]
agent = "code-reviewer"
task = "Review the implementation for correctness and style."
block_threshold = "major"
```

### WorkflowExecution

A recursive task tree for complex verification. A post-check can itself be a workflow with its own tasks, dependencies, and checks. This allows arbitrarily deep verification chains.

### Check results and auto-fix

Each check produces a `CheckResult` with:

- `passed` -- boolean verdict
- `message` -- human-readable explanation
- `details` -- optional structured data (issues, criteria review, etc.)
- `fix` -- optional callback that attempts to fix the failing condition

When a check fails and provides a fix callback, the runner applies the fix and re-runs the check automatically. If the re-run still fails, the check result is final.

Failed post-checks allow the agent to **retry** (the iteration primitive). The `retry` field on a task spec sets the maximum number of retry attempts. After exhausting retries, the task escalates to its parent with an `EscalationPayload` containing all failed check results and the agent's summary.

## Write safety model

The write-safety module prevents data corruption when multiple agents operate on the same files concurrently. It provides four mechanisms:

### WriteQueue

Per-path serialization using asyncio locks. Only one write to a given file path can proceed at a time. The queue resolves paths to canonical form so that different path representations (relative, absolute, symlinked) all serialize against the same lock.

```python
async with write_queue.lock(path):
    await atomic_write(path, content)
```

### StaleWriteTracker

Content-hash-based detection of stale reads. When an agent reads a file, the tracker records a SHA-256 hash of the content keyed by session ID and path. When the agent later writes to that file, the tracker compares the recorded hash against the current on-disk hash. If they differ, another agent modified the file since this agent read it, and the write is rejected with a `StaleWriteError`.

A session that has never read a path cannot write to it (hard error). New file creation bypasses this check.

### atomic_write

Writes go through a temporary file with `fsync`, then atomically rename to the target path. This prevents partial writes from appearing at the target path -- the file is either fully written or not present.

### Transient retry

The `with_transient_retry` wrapper retries operations that fail with transient OS errors (EIO, EBUSY, EAGAIN, ENOSPC, ENOLCK) using exponential backoff. Non-transient errors propagate immediately.

### Integration with tools

Write tools in the tool module go through the full write-safety pipeline:

1. Acquire the write queue lock for the target path
2. Check the stale-write tracker (was the file read by this session? has it changed since?)
3. Perform the write atomically
4. Update the tracker with the new content hash

## Budget enforcement

Budgets are denominated in **USD** using an orxtra-maintained internal pricing table. The system tracks actual token usage and computes costs per model, not per token count alone -- different models have different per-token prices.

### How costs accumulate

The session module's `compute_cost_usd` function maps a model key (e.g., `anthropic/claude-sonnet-4-6`) and a `Usage` record (input tokens, output tokens) to a dollar amount. The scheduler accumulates costs per task as each LLM turn completes.

### Budget limits

Budgets can be set at two levels:

- **Task level**: the `budget` field on a `TaskSpec` sets a per-task spending cap
- **Agent level**: the `budget` field on an `Agent` definition sets a default cap for all tasks using that agent

A task-level budget overrides the agent-level default.

### Threshold events

When cumulative spend crosses 80% of the budget, the scheduler fires a `BudgetThresholdCrossed` event to the Overseer. This gives the Overseer a chance to intervene (reallocate budget, prioritize remaining work, etc.) before the budget is fully exhausted.

### Exhaustion policies

When the budget is fully exhausted, the `BudgetExhaustionPolicy` determines what happens:

- `block_new` -- prevent new tasks from starting, but let active tasks finish
- `cancel_all` -- abort the entire run immediately
- `timeout_grace` -- block new tasks and schedule a forced abort after 60 seconds
- `unlimited` -- no enforcement (the budget is informational only)

The scheduler fires a `BudgetExhausted` event to the Overseer regardless of which policy is active.

## Action tools

Action tools are the Overseer's governance instruments. While regular agents have read/write/edit/git tools for working with files, the Overseer has a distinct set of tools for managing the run's structure, recording decisions, and communicating with human operators.

### Governance tools

- `create_workflow` -- create a new goal-oriented task tree. The Overseer decomposes high-level goals into concrete tasks with agents, dependencies, and checks.
- `create_task` -- create a single subtask within the current active task.
- `record_decision` -- record a decision with rationale in the trace store. Captures the decision type, the choice made, and why.
- `add_constraint` -- add a mechanical or advisory constraint. Mechanical constraints (e.g., `tests_pass`, `lint_clean`, `no_removed_exports`) are enforced automatically by the scheduler. Advisory constraints are reported to the Overseer.
- `record_assumption` -- record an assumption, optionally creating an inbox item for human verification.
- `create_inbox_item` -- create a human inbox item for escalation. Includes the question, options, an assumed option (work proceeds under this assumption), and the impact if the assumption is contradicted.
- `write_lesson` -- write to the cross-run knowledge base. Lessons can be permanent or scoped, and are tagged for relevance-based retrieval.
- `update_workflow_status` -- update the Overseer's health assessment of a workflow.

### Mechanical constraints

Constraints added via `add_constraint` with `tier: mechanical` are enforced by the scheduler. The constraint kinds are:

| Kind | What it checks |
|---|---|
| `tests_pass` | Runs pytest and verifies exit code 0 |
| `lint_clean` | Runs ruff and verifies exit code 0 |
| `no_removed_exports` | Snapshots exports before the task and verifies none were removed |
| `no_changed_signatures` | Snapshots function signatures before the task and verifies none changed |
| `no_new_dependencies` | Checks that dependency files (pyproject.toml, requirements.txt, etc.) did not change |
| `no_new_files_outside` | Verifies no new files were created outside a specified directory |

Expensive constraints (`tests_pass`, `lint_clean`) run only on workflow completion (when the completing task has subtasks). Cheap constraints run after every task.

## Event subscriptions

The dispatch module provides a persistent event delivery system. Events are stored in the trace store. Subscriptions define filters that match events and action chains that execute when events match.

### Subscriptions

A subscription consists of:

- A `FilterPredicate` that determines which events match
- One or more `SubscriptionAction` entries that execute when an event matches
- An owning `principal_id` (the actor who created the subscription)

Filter predicates are AND-combined:

- `event_types` -- if set, the event's type must be in the list
- `sources` -- if set (a list of source slugs), the event must come from one of those sources
- `principal_id` -- if set, the event's principal must match exactly
- `data_predicates` -- reserved for future jsonb matching

### Action types

Each subscription action specifies an `Action` to execute:

- `ScriptAction` -- call a Python function (`module:function` format)
- `LogAction` -- log a message at a specified level
- `WorkflowAction` -- start a workflow execution
- `EventAction` -- fire a new event (the derived event is attributed to the subscription owner's principal)
- `NotifyAction` -- deliver a notification to a target principal

### Accumulator buffering

Subscription actions can include an `accumulator_config` for batching. Instead of executing immediately on every matching event, events are buffered and flushed when a threshold is reached:

- **Count threshold**: flush when the buffer reaches N events
- **Time threshold**: flush after N seconds since the first buffered event
- Whichever fires first wins

This enables patterns like "aggregate all deployment events over 5 minutes and send a single summary notification."

### Dual-phase delivery

Events flow through two parallel systems:

- **Transient delivery**: in-memory asyncio Futures for `wait_for` task waking. Events fired before any waiter registers are silently lost (no replay). Used for internal task coordination.
- **Persistent delivery**: the DispatchWorker polls the events table, matches against subscriptions, and executes actions with at-least-once delivery semantics. Completion records prevent duplicate execution.

### Self-subscriptions

Consumer principals created with `notification_event_types` automatically get notification subscriptions for those event types. This makes notification preferences a special case of the general subscription system rather than a separate mechanism.

## Principal-based attribution

Every actor in orxtra has a durable identity called a **Principal**. Every event, every run, every subscription traces back to the principal that created or emitted it. There are no anonymous mutations.

### Principal kinds

The framework defines four built-in kinds:

- `run` -- a workflow execution
- `consumer` -- an API client
- `source` -- a webhook source
- `system` -- the singleton system principal (used for machinery-generated events)

Applications can register additional kinds (e.g., `user`) via `ServerConfig.principal_kinds`. An unknown kind is a hard error at the service layer -- the KindRegistry validates at the instance level.

### Identity at birth

Principals are minted at creation, transactionally adjacent to the entity they represent. A run mints its principal before the run record is created. A source mints its principal before the source record. An orphaned principal from a crashed creation is harmless and cleaned up by recovery (age-guarded). There is never a half-created actor.

### Attribution vs. ownership

History-bearing foreign keys to principals use `RESTRICT`:

- `events.principal_id` -- the actor that emitted the event
- `runs.created_by` -- the actor that started the run
- `sources.created_by` -- the actor that registered the source
- `inbox_items.resolved_by` -- the actor that answered/skipped/rejected the inbox item
- `consumers.principal_id` -- the consumer's identity

An actor with history is undeletable (raises `PrincipalInUseError`). A principal that never acted deletes cleanly.

Operational state uses `CASCADE`:

- `subscriptions.principal_id` -- the subscription dies with its owner

### AuthContext vs. Principal

`AuthContext` is the ephemeral per-request authentication context. It is created when a credential is verified, carries scopes and trust tier, and is never persisted. It resolves to a Principal for attribution.

`Principal` is the durable identity record in the `principals` table. It persists across requests and is the FK target for all attribution.

The `resolve_caller_principal` function bridges the two: it maps the ephemeral `AuthContext` to the persisted `Principal` that the caller represents.

### Scope-based authorization

Every `Capability` (an API operation) declares a `required_scope`. The dispatch choke point verifies that the caller's `AuthContext` carries the required scope before routing to the service function. Scopes are coarse strings like `runs:read`, `runs:manage`, `inbox:respond`, `events:write`, `subscriptions:manage`.

An API mounted without an authenticator serves public surfaces only -- it cannot dispatch capabilities.

### Trust tiers

The `TrustTier` enum defines four levels:

- `anonymous` -- no credentials presented
- `identified` -- credentials presented but not verified
- `verified` -- credentials verified
- `system` -- the local operator path (CLI); full trust, attributed to the system principal

## Categories and scheduling

Categories are orxtra's mechanism for **model routing** -- mapping agent definitions to specific LLM providers and models without hardcoding provider details in every agent file.

### Category definitions

A categories file maps symbolic names to `provider/model` strings:

```toml
format_version = 1

[categories]
default = "anthropic/claude-sonnet-4-6"
reasoning = "anthropic/claude-opus-4-6"
fast = "openai/gpt-4o-mini"
```

### Agent routing

Each agent declares either a `category` or an explicit `provider`/`model` pair, but not both:

```toml
# Category-based routing (resolved via the categories file)
[agent]
name = "reviewer"
category = "reasoning"

# Explicit routing (no category resolution needed)
[agent]
name = "formatter"
provider = "openai"
model = "gpt-4o-mini"
```

The `resolve_category` function looks up the agent's category in the categories map and returns the `provider/model` string. An unknown category is a hard error.

### Why categories matter

Categories decouple agent definitions from infrastructure decisions:

- **Multi-provider flexibility**: the same workflow can use agents on different providers (Anthropic, OpenAI, Google) without changing agent files. Swapping a provider means editing one line in the categories file.
- **Cost control**: categories like `fast` and `reasoning` express intent. A cost-sensitive deployment maps `fast` to a cheaper model; a quality-sensitive deployment maps both to the best available model.
- **Budget computation**: the scheduler uses the resolved `provider/model` string to look up per-token pricing and compute USD costs for budget enforcement.

### Scheduling

The scheduler uses the resolved model information from categories for:

- **Cost accumulation**: each LLM turn's token usage is priced according to the model's entry in the internal pricing table
- **Budget enforcement**: cumulative costs are tracked per task and compared against the task or agent budget limit
- **Structural advisories**: the scheduler detects read-only agents (no write tools) and advises the Overseer that they could be front-loaded for earlier execution
