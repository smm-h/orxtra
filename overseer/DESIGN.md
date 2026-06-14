# Overseer Module Design

The Overseer is the brain of the system. One persistent agent with action tools, structured memory in PostgreSQL, and the root task's agent. It observes everything, makes judgment calls by taking actions via tools, and drives intent to completion. It never mutates files, runs commands, or executes tasks directly.

## Responsibility

Drive intent to completion. The Overseer receives the user's intent at the start of a run and makes every judgment call needed to fulfill it: what to do, how to decompose it, when to retry, when to escalate, how to allocate budget, what assumptions to make. It creates workflows and tasks via action tools. It monitors execution by handling events from the scheduler.

## What the Overseer Is

- The agent of the root task -- the top of the task hierarchy for the entire run
- One persistent LLM session that spans the run's lifetime
- Has read-only file tools (read, grep, glob) for codebase inspection
- Has `consult` for spawning read-only research agents
- Has action tools for system-level mutations (create_workflow, add_constraint, etc.)
- Has `start_task` / `end_task` for participating in the task lifecycle
- Has structured memory in PostgreSQL (decisions, constraints, assumptions, lessons)
- Receives session handoff when context approaches the model's window limit

## What the Overseer Is NOT

- Not an executor. It never runs tasks, manages state machines, or enforces timeouts. That is the scheduler.
- Not a worker. It never writes files, runs tests, or produces code. That is task agents.
- Not a router. It does not pick models or resolve categories. That is the agent module and scheduler.

## Correctness Over Convenience

The Overseer's system prompt encodes a systemic bias toward correctness:

- **Prefer the most correct solution regardless of effort.** Never factor in effort when evaluating options. Never recommend deferring work. Never overestimate effort as a reason to avoid the right approach.
- **When creating inbox items, always include the hard-but-right option.** Every inbox item's `options` array must contain the most correct solution, even if it requires significant work.
- **Structural advisories are suggestions, not commands.** When the scheduler sends a `StructuralAdvisory` (e.g., "consider front-loading read-only tasks"), the Overseer evaluates whether the suggestion makes sense in context. It should not blindly follow mechanical advice -- it has context the scheduler does not. It should not blindly ignore it either.
- **When an inbox item is rejected**, the Overseer must re-investigate via tools (read/grep/glob/consult) and create a new inbox item with genuinely better options. Resubmitting the same options with different wording is not acceptable.

## Tools

### Read-Only File Tools

| Tool | Purpose |
|---|---|
| `read` | Read file contents |
| `list_dir` | List directory contents |
| `grep` | Search file contents |
| `glob` | Find files by pattern |

### Delegation Tools

| Tool | Purpose |
|---|---|
| `consult` | Spawn a read-only research agent |

### Task Lifecycle Tools

| Tool | Purpose |
|---|---|
| `start_task` | Enter a task (pre-checks run) |
| `end_task` | Complete a task (post-checks run) |
| `create_workflow` | Create a goal-oriented task tree with goals and postchecks |
| `create_task` | Create a concrete subtask with agent, prompt, and checks |
| `create_wait_for` | Create a wait-for task (blocks until named event or timeout) |

### Memory Tools

| Tool | Purpose |
|---|---|
| `record_decision` | Record a decision with rationale in the decisions table |
| `add_constraint` | Add a mechanical or advisory constraint |
| `record_assumption` | Record an assumption, optionally create inbox item |
| `write_lesson` | Write to the cross-run knowledge base |

### Context Tools

| Tool | Purpose |
|---|---|
| `notepad` | Write entries to the run's shared notepad |

### System Tools

| Tool | Purpose |
|---|---|
| `create_inbox_item` | Create a human inbox item for escalation |
| `update_workflow_status` | Update the Overseer's health assessment of a workflow |

Tool parameter schemas are defined in `orxt.protocols`. Implementations live in this module's `_tools.py`.

## Interaction Model

The Overseer interacts with the scheduler through its persistent session. See `protocols/DESIGN.md` for the full specification.

1. The scheduler detects an event (run started, task failed, budget crossed, inbox answered, etc.)
2. The scheduler sends a structured event message to the Overseer's session
3. The Overseer's tool-call loop runs: inspect files, research via consult, take actions via tools
4. The Overseer produces a text response summarizing what it did
5. The scheduler verifies the Overseer's actions mechanically:
   - Created workflows/tasks pass schema validation
   - New constraints do not contradict existing ones
   - This is not the same action taken for the same failure last time (repetition check)
   - Budget allocation is proportional to remaining work
6. If verification passes: the event is handled
7. If verification fails: failure details sent back to the Overseer in the same session
8. Loop up to N times. Degraded mode if the Overseer cannot satisfy verification.

### Event Types

Events the scheduler sends to the Overseer (defined in `orxt.protocols._events`):

| Event | When | Typical Overseer Response |
|---|---|---|
| `RunStarted` | Run begins | Create initial workflow via `create_workflow` |
| `TaskFailed` | Task's postchecks failed, agent gave up | Create fix task, adjust constraints, escalate to human |
| `TaskEscalated` | Child task failed and escalated | Retry strategy, re-decompose, escalate further |
| `BudgetThresholdCrossed` | Budget approaching limit | Reallocate from completed workflows, increase, or abort |
| `BudgetExhausted` | Budget depleted | Abort or escalate to human |
| `InboxAnswered` | Human responded | Update assumptions, adjust if contradicted |
| `InboxRejected` | Human rejected options as insufficient | Re-investigate via tools, create new inbox item with better options |
| `StructuralAdvisory` | Scheduler detected structural improvement | Evaluate suggestion, modify task tree if warranted, or dismiss with rationale |
| `HealthDegraded` | Overseer health metrics crossed threshold | Acknowledge, adjust behavior |

## Memory: PostgreSQL Tables

Structured, queryable store. Tables owned by the trace module, read/written by the Overseer via action tools.

| Table | Key Columns | Notes |
|---|---|---|
| `decisions` | id, run_id, decision_type, choice, rationale, outcome | outcome updated when known |
| `constraints` | text, source_decision_id, active, tier | tier is `mechanical` or `advisory` |
| `assumptions` | text, status, scope, inbox_item_id | status: pending / confirmed / contradicted |
| `lessons` | text, relevance_tags, permanent, source_file | primary store for cross-run knowledge |
| `overseer_workflow_status` | workflow_id, current_step, health | overwritten, not appended |

The Overseer can self-serve context by querying its memory via read-only file tools and consult agents. The scheduler also assembles relevant context from these tables when constructing event messages.

## Constraint Tiers

Mechanical constraints use a closed vocabulary of checkable primitives:
- `tests_pass` -- test suite must pass (always implicitly active)
- `lint_clean` -- linter must pass (always implicitly active)
- `no_removed_exports(glob)` -- public API symbols cannot be removed from matching files
- `no_changed_signatures(glob)` -- function/method signatures cannot change
- `no_new_dependencies` -- no additions to dependency manifests
- `no_new_files_outside(directory)` -- no file creation outside specified directory

Cheap mechanical constraints (no_removed_exports, no_changed_signatures, no_new_dependencies, no_new_files_outside) run after every task. Expensive constraints (tests_pass, lint_clean) run at workflow completion only. All run as built-in postchecks after the consumer's postchecks. Violations are immediate failures.

Advisory constraints are freeform text included in agent task context. Not mechanically enforced.

## Health Monitoring

The scheduler tracks Overseer health metrics per event type:
- Post-check failure rate (Overseer's actions failed mechanical verification)
- Repetition rate (same action for same failure type)
- Tool-call error rate (action tools returned errors)

If any rate exceeds threshold over a rolling window, the scheduler enters degraded mode for that event type:

| Event Type | Degraded Behavior |
|---|---|
| TaskFailed / TaskEscalated | Fixed escalation ladder: retry same context, then wider context, then mark failed |
| BudgetThresholdCrossed | Maintain current allocations |
| Any | Escalate to human inbox |

The Overseer is not disabled -- it is bypassed for the failing event type only. Other event types continue normally. If rates recover, degraded mode exits automatically.

Overseer calls are funded from the triggering workflow's budget.

## Session Handoff

The Overseer is the only entity that receives session handoff. Task agents are scoped and short-lived.

When the Overseer's conversation approaches ~90% of the model's context window:

1. The scheduler detects the threshold (it tracks token usage).
2. The scheduler asks the Overseer to produce a detailed summary.
3. The transcript is already fully persisted in PG.
4. A new Overseer session is created with the summary as initial context plus the old session's UUID for querying the full transcript.

## Context Assembly for Task Agents

Before each agent task (when `context_refinement = true`), the scheduler sends a context-refinement event to the Overseer. The Overseer can select relevant lessons, request additional code context, reorder, or accept as-is.

When `context_refinement = false`, the agent receives layers 1-2 only (task template + runtime context: constraints, notepad, prior failures). No Overseer call.

The scheduler stores pre-refinement context and a unified diff of the Overseer's changes via the trace module's `context_diffs` table.

## Consumer Knowledge Loading

The Overseer owns loading consumer knowledge files from the `knowledge/` directory at the start of each run.

**Markdown files** (`.md`) -- free-form domain knowledge. Written to the lessons table as permanent entries via `write_lesson`.

**TOML files** (`.toml`) -- structured constraints:

```toml
[[constraints]]
text = "All generated code must pass lint and type checks before commit"
tier = "mechanical"
```

Written to the constraints table via `add_constraint`.

Consumer knowledge is loaded once at run start. Files are re-loaded only if their content hash changes since the last run.

## Cross-Run Learning

The `lessons` table is the primary store for cross-run knowledge. The Overseer reads it at the start of every run. Entries have timestamps and source file paths.

- The scheduler checks via git whether source files have changed and flags stale entries
- Entries expire after N runs unless explicitly confirmed by a human
- Contradicting evidence marks entries as disputed and escalates
- Permanent entries are human-curated or loaded from consumer knowledge files

The `knowledge-module/` (experimental) additionally indexes lessons into a cognee knowledge graph for semantic retrieval. When enabled, context assembly receives results from both flat SQL and cognee. See `knowledge-module/DESIGN.md`.

## Coherence Summary

At the end of a run, the Overseer reviews the full accumulated diff against the original intent. It scores whether the changes accomplish what was asked, flags gaps, and notes unexpected side effects. Written to the run's `coherence_summary` field.

## Autonomy Knob

Single scalar. Mechanical action-type rules, not Overseer judgment.

| Level | Overseer handles autonomously |
|---|---|
| Low | Read-only decisions only. Almost everything escalated to human inbox. |
| Medium | Retry, budget reallocation, concurrency, task-level assumptions. Scope changes, architecture decisions, understanding-level assumptions escalate. |
| High | Everything except: modifying external API contracts, changing auth/security flows, deleting data, deploying, adding new external dependencies. |
| Max | Everything. Human inbox empty. Report is audit trail. |

Each level maps to an explicit list of action types that are autonomous vs escalated. The mapping is published and deterministic. Can change mid-run.

**Action-gating**: irreversible actions (deploy, delete data, external sends) are forbidden below the configured autonomy level by the scheduler's action-type mapping. They require an answered approval inbox item to proceed.

## Error Taxonomy

The scheduler classifies every failure before sending it to the Overseer:

| Category | Pattern | Example |
|---|---|---|
| infra | Timeout, network error, disk full, OOM, transient API errors exhausted | ETIMEDOUT, No space left |
| context_limit | Token limit exceeded for the model | Context too large |
| parse | LLM output did not match schema | JSON parse error, missing field |
| flaky | Non-deterministic test failure | Test passed on re-run without changes |
| build_env | Missing dependency, wrong version | ModuleNotFoundError |
| logic | Consistent test failure from code error | AssertionError |
| unclassified | No pattern matched | |

The Overseer sees the classification and applies type-appropriate strategies.

## Files

| File | Contents |
|---|---|
| `_overseer.py` | `Overseer` class. Manages the persistent session, handles events from the scheduler, takes actions via tools. |
| `_tools.py` | Action tool implementations. Each tool calls the trace module's write API. Parameter validation uses schemas from `orxt.protocols._tools`. |
| `_memory.py` | Context queries against PG tables (decisions, constraints, assumptions, lessons, workflow status). Used for self-serve context and event message assembly. |
| `_health.py` | Health monitoring. Tracks post-check failure rate, repetition rate, tool error rate per event type. Degraded mode logic. |
| `_handoff.py` | Session handoff detection and execution. |
| `_inbox.py` | Human inbox item creation. Structured async queue for escalations and assumptions. |
| `_autonomy.py` | Autonomy knob. Level definitions, action-type-to-level mapping, escalation routing, action-gating enforcement. |
| `_learning.py` | Cross-run knowledge base queries against the lessons table. Staleness detection via git, expiry after N runs. |
| `_knowledge.py` | Consumer knowledge file loading. Reads .md and .toml files from the knowledge directory, writes to lessons table and constraints table via trace. |

Note: error classification logic lives in the scheduler (which does the classifying), not in the overseer. The `ErrorCategory` enum is in `orxt.protocols._errors`.

## What This Module Does NOT Do

- Does not execute tasks or manage state machines (that is the scheduler)
- Does not write files, run commands, or produce code
- Does not validate task/workflow schemas (that is the scheduler, using tool-level validation)
- Does not manage transport connections (that is the session module)
- Does not own the database schema (that is the trace module)
