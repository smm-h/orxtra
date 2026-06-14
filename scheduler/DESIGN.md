# Scheduler Module Design

The scheduler is the nervous system. A deterministic event loop that manages the recursive task hierarchy, enforces budgets and constraints, runs pre-checks and post-checks, routes events to the Overseer, and handles crash recovery. It has no opinions. It does what it is told.

## Responsibility

- Manage the recursive task hierarchy (tasks within tasks, no depth limit)
- Run pre-checks before `start_task` and post-checks before `end_task` (via the verify module)
- Enforce that all tool calls occur within an active task (hard error otherwise)
- Handle runtime task creation (`create_task` / `create_workflow` tool calls from agents)
- Route events to the Overseer (task failed, budget crossed, human responded)
- Verify the Overseer's actions mechanically after each event-handling turn
- Enforce budgets (USD-based, per-task and per-workflow) and mechanical constraints
- Manage transport registry for multi-provider model routing
- Manage crash recovery via the trace module
- Manage write safety (per-path write queue, stale-write detection, transient replay)

## Task Hierarchy

Everything is a task. Tasks nest recursively. A run is the root task. A workflow is a task containing subtasks. A leaf task is executed by an agent.

### Task Lifecycle

```
created -> prechecking -> active -> postchecking -> completed
                |                       |
                v                       v
         precheck_failed         postcheck_failed -> active (agent retries)
                                        |
                                        v
                                    escalated
```

- `prechecking`: the scheduler runs the task's pre-checks (via verify module). If all pass, task transitions to `active`. If any fail, the error is returned to the agent that called `start_task`.
- `active`: the agent is working. All tool calls are permitted within this task.
- `postchecking`: the scheduler auto-commits any uncommitted file changes via safegit (using the `message` from `end_task` as the commit message), then runs the task's post-checks against the committed state. If all pass, task transitions to `completed`. If any fail, the error is returned to the agent that called `end_task`.
- `postcheck_failed -> active`: the agent receives the failure, fixes its work, and calls `end_task` again.
- `escalated`: the agent cannot satisfy post-checks. The failure is packaged as an `EscalationPayload` (defined in `orxt.protocols`) and delivered to the parent task's agent.

### start_task / end_task Enforcement

All tool calls (including read-only) require an active task. The scheduler tracks which task is active per agent session. A tool call without an active task is a hard error.

Exception: `start_task` itself can be called without an active task -- it is how the agent enters one. The task_id is injected into the agent's prompt by the scheduler at spawn time.

### Runtime Task Creation

Agents create subtasks via `create_task` (concrete work) or `create_workflow` (goal-oriented decomposition):

- `create_task`: the agent specifies an agent definition, task prompt, pre-checks, post-checks, variables, timeout, budget, and write paths. The scheduler validates the specification, creates the task as a child of the agent's current active task, and spawns the specified agent.
- `create_workflow`: the agent specifies goals, description, post-checks, and budget. The scheduler creates the workflow task and assigns a workflow agent to decompose it into subtasks.

Both tools return a task/workflow ID. The parent agent can continue working while subtasks execute. When the parent calls `end_task`, the scheduler verifies that all subtasks within the parent's scope are completed. Incomplete subtasks block `end_task`.

### Escalation

When an agent cannot satisfy a task's post-checks (exceeds retry limit or gives up), the scheduler:

1. Packages the failure as an `EscalationPayload`: task name, agent name, attempt count, failed check results, agent summary, task context
2. Delivers the payload to the parent task's agent as a structured message in its session
3. The parent agent decides: create a new subtask to fix the problem, adjust constraints, escalate further, or abort

If the escalation reaches the root task (the Overseer), the Overseer handles it via its action tools. If the Overseer cannot handle it, the scheduler enters degraded mode or escalates to the human inbox.

## Task Schema

Each task, whether declared in a workflow TOML or created at runtime via `create_task`, uses the `TaskSpec` schema from `orxt.protocols._task`. See `protocols/DESIGN.md` for the full field list.

Key fields:

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique task identifier within its parent |
| `prechecks` | list of Executions | no | Must pass before `start_task` succeeds |
| `postchecks` | list of Executions | no | Must pass before `end_task` succeeds |
| `agent` | string | yes* | Agent definition name (mutually exclusive with callable/subtasks) |
| `task_prompt` | string | yes* | Task prompt template |
| `callable` | string | yes* | Python callable path (function tasks) |
| `subtasks` | list of TaskSpec | yes* | Nested tasks (composite/workflow tasks) |
| `variables` | list of strings | yes | Variable names this task needs |
| `depends_on` | list of strings | no | Tasks that must complete first (declared in `[dependencies]` section for TOML, or inline for runtime) |
| `timeout` | integer | yes (agent tasks) | Max wall-clock seconds |
| `context_refinement` | boolean | yes (agent tasks) | Whether the Overseer refines context before this task |
| `retry` | integer | no | Max retry count (default 0) |
| `retry_resume` | boolean | conditional | Required when retry > 0 |
| `retry_inject_failure` | boolean | conditional | Required when retry > 0 |
| `for_each` | string | no | Variable name resolving to a list |
| `for_each_abort_on_failure` | boolean | conditional | Required when for_each is set |
| `output_schema` | string | no | JSON Schema path for structured output validation |
| `budget` | number | no | Per-task USD budget |
| `write_paths` | list of strings | no | File paths this task may write |
| `on_success` | string | no | Python callable path, runs after postchecks pass (non-fatal) |
| `pre_retry` | string | no | Python callable path, runs before retry (exceptions abort retry) |

*A task must declare exactly one of: `agent` + `task_prompt`, `callable`, `subtasks`, `wait_for`, or `decision_point`.

Dependencies are declared in a `[dependencies]` section at the end of the workflow TOML (after all tasks are listed), so the LLM can see all task names before declaring the graph. Tasks not listed in the dependencies section have no dependencies and can run immediately. For runtime task creation via `create_task`, `depends_on` is an optional parameter.

## Structural Advisories

The scheduler analyzes the task tree and surfaces structural observations to the owning agent as `StructuralAdvisory` events:

- Read-only tasks mixed with implementation tasks that could be front-loaded
- Dependency ordering that could be improved
- Parallelization opportunities
- Tasks whose dependencies suggest a different ordering than declared

**The scheduler never silently rearranges the task tree.** All structural observations are advisory. The owning agent evaluates the suggestion and decides whether to act. The scheduler presents; the agent decides.

## Overseer Interaction

The scheduler sends events to the Overseer and verifies the Overseer's responses. See `protocols/DESIGN.md` for the interaction model specification.

The scheduler:
1. Detects events (task failed, budget crossed, run started, inbox rejected, structural advisory, etc.)
2. Sends structured event messages to the Overseer's persistent session
3. After the Overseer's tool-call loop completes, verifies its actions:
   - Schema validation on created workflows/tasks
   - Constraint consistency (new constraints do not contradict existing ones)
   - Repetition check (not the same action for the same failure)
   - Proportionality check (budget allocation proportional to remaining work)
4. If verification fails, sends failure back to the Overseer for correction
5. Enters degraded mode per event type if Overseer cannot satisfy verification after N attempts

## Context Assembly for Agent Tasks

Before each agent task, the scheduler builds context in layers:

**Layer 1: Task declaration.** The task prompt template with `{variable}` placeholders substituted.

**Layer 2: Runtime system context.** Mechanically appended:
- Active constraints (both mechanical and advisory)
- Prior failed attempts with full structured failure context (if retrying with `retry_inject_failure = true`)
- Notepad content from the current run

**Layer 3: Overseer refinement.** Only when `context_refinement = true`. The scheduler sends the assembled context to the Overseer. The Overseer can select relevant lessons, request additional code context, reorder, or accept as-is.

The scheduler stores pre-refinement context and a unified diff of the Overseer's changes via the trace module's `context_diffs` table.

## Dependencies and Parallelism

The scheduler builds a dependency graph from the `[dependencies]` section in TOML or `depends_on` parameters on runtime-created tasks. Tasks with no dependency between them run in parallel via asyncio. `for_each` iterations are also parallel.

Topological sort at load time. Cycles are hard errors.

## Per-Task Budgets

Each task (including workflows) can have a USD budget. Budget is the natural depth limit for task nesting. When a task's accumulated cost approaches its budget, the scheduler sends a `BudgetThresholdCrossed` event to the Overseer.

Cost is computed from token counts using the internal pricing table (in the session module).

## Mechanical Constraint Enforcement

After each task's postchecks pass but before the task transitions to `completed`, the scheduler runs all active mechanical constraints as built-in postchecks. These run after the consumer's postchecks, acting as a final guardrail.

Cheap constraints (no_removed_exports, no_changed_signatures, no_new_dependencies, no_new_files_outside) are fast static checks and run after every task. Expensive constraints (tests_pass, lint_clean) are scoped to workflow completion -- they run only when the outermost workflow task completes, not after every leaf task. This prevents the test suite from running 20+ times per workflow.

Violations are immediate task failures.

## Transport Registry

The scheduler holds a `dict[str, Transport]` keyed by provider name. When spawning an agent for a task, it parses the `"provider/model"` string from the resolved category, looks up the transport, and creates a session. Missing provider is a hard error.

## Function Tasks

A function task runs a Python callable instead of an LLM agent. The callable receives a `TaskContext` and returns a `TaskResult`. Function tasks do not support `category`, `timeout`, `context_refinement`, or agent-specific postchecks.

## Structured Output

Tasks with `output_schema` get JSON extraction and validation after completion. Parse failure or validation failure triggers retry. Validated output available as `{task_name_output}`.

## Batch Iteration (for_each)

Tasks with `for_each` execute once per item in a list variable. This is the SPT iteration construct -- not subtask creation, not retry attempts. Each run is an **iteration**: the same task spec executing with different `{item}` data.

Iterations run in parallel, bounded by `max_concurrency` (required when `for_each` is set). Each iteration has its own lifecycle (start_task/end_task), its own postchecks, and its own output. Iterations are persisted as iteration rows linked to the for_each task, each with status, output, and check results.

`for_each_abort_on_failure` controls whether a failed iteration aborts remaining iterations. The output variable `{task_name_output}` is an array of per-iteration results (null for failed iterations when `for_each_abort_on_failure = false`).

## Post-Task Actions

**`on_success` callable.** Runs after postchecks pass. Receives a `TaskContext`. Exceptions are logged but do not fail the task.

**`pre_retry` callable.** Runs before a retry attempt. Receives a `TaskContext`. For cleaning up state. Exceptions abort the retry.

## Task Output Propagation

When task B depends on task A, B has access to A's outputs:

- `{task_a_output}` -- structured output (validated JSON)
- `{task_a_text}` -- raw agent response
- `{task_a_result}` -- metadata: `{passed, duration_seconds, retries_used}`

Variable name collisions between task outputs and workflow variables are hard errors.

## Wait-For Tasks

A wait-for task blocks until a named event fires or the timeout expires. Internal events (inter-workflow) use PG NOTIFY directly. External events use the services API's `fire_event`. If the event fires before timeout, the task succeeds and the event payload is available as `{task_name_event}`. If timeout expires, the task fails.

Wait-for tasks are TOML-only (defined in workflow TOML by the Overseer) with one exception: `create_wait_for` is available as a runtime tool for agents that need to create synchronization points.

## Decision Point Tasks

A decision point pauses execution and sends an event to the Overseer. The Overseer sees the current state and decides: continue as-is, modify remaining tasks, or abort.

## Services

Workflows can declare long-running processes with start/health-check/stop commands. The scheduler manages their lifecycle.

## File-Lock Registry

When multiple workflows run concurrently, the file-lock registry prevents conflicting file writes. Claims are inferred from `write_paths` declarations. If an agent task needs a file outside its claims, the scheduler sends a scope event to the Overseer.

## Write Safety Enforcement

The scheduler instantiates write-safety infrastructure from `orxt.write_safety` at run start (write queue, stale-write tracker) and passes it to tool constructors. It re-constructs scoped tools per task when `write_paths` is declared. See `write-safety/DESIGN.md` for the mechanisms.

## Crash Recovery

Three-pass idempotent startup recovery (see `trace/DESIGN.md`):
1. Reclaim interrupted tasks (transition to `failed`)
2. Re-evaluate blocked/gated work
3. Clean orphaned resources (services, locks, stale advisory locks)

## Pause/Resume

Pause transitions the run to paused state via trace. Resume reloads task states from the database and resumes pending/ready tasks. The task states in PG are the cursor -- no separate cursor structure is needed. Pauseable at every level (task, workflow, entire run).

## Cancellation

Catches `CancelledError`, cleans up sessions, writes partial results.

## Files

| File | Contents |
|---|---|
| `_types.py` | Re-exports `TaskContext`, `TaskResult` from `orxt.protocols._task`. Plus scheduler-specific: `WorkflowConfig` (parsed TOML), `ServiceConfig`. All pydantic models with `strict=True, extra='forbid'`. |
| `_loader.py` | `load_workflow(path_or_str)` -- reads workflow TOML, validates schema, returns task tree. |
| `_graph.py` | Dependency graph construction. Topological sort. Cycle detection. Parallelizable task identification. |
| `_executor.py` | `Scheduler` class. Event loop, task execution, start_task/end_task enforcement, active-task tracking, pre/postcheck dispatch via verify, timeout enforcement, retry logic, constraint enforcement, budget tracking, pause/resume, `abort()`. Write safety orchestration. |
| `_validator.py` | Task tree validation: schema checks, structural checks (dependency cycles, mutual exclusivity). |
| `_overseer.py` | Overseer interaction: send events, receive responses, verify actions, degraded mode per event type. |
| `_services.py` | Service lifecycle management. Start, health-check, stop. |
| `_locks.py` | File-lock registry. Claim management, conflict detection, release on completion. |
| `_events.py` | Event registry for wait-for tasks. Event registration, firing, listener management. PG LISTEN/NOTIFY integration. |

## What This Module Does NOT Do

- Does not make judgment calls (that is the Overseer)
- Does not generate workflows or decompose goals (that is the Overseer and workflow agents)
- Does not define agents or tools (those are loaded externally)
- Does not create transport connections (those are constructed by the services layer and passed in via the transport registry)
- Does not implement model selection logic (that is agent category resolution)
- Does not compute USD costs (reads the pricing table; computation is mechanical)
