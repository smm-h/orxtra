# Overseer Module Design

The Overseer is the brain of the system. One persistent agent with read-only tools, a long memory, and structured decision protocols. It observes everything, decides what should happen, and orders short-lived agent steps to do the work. It never mutates files, runs commands, or executes tasks directly.

## Responsibility

Drive intent to completion. The Overseer receives the user's intent at the start of a run and makes every judgment call needed to fulfill it: what to do, how to decompose it, when to retry, when to escalate, how to allocate budget, what assumptions to make. It produces workflows (pipeline definitions) that the scheduler validates and executes. It monitors execution and reacts to events (step failures, budget thresholds, human responses).

## What the Overseer Is

- One agent with persistent context across the entire run
- Has read-only tools (read, grep, glob -- inspection, not mutation)
- Has a structured memory (SQLite database)
- Makes decisions via typed protocols with closed output schemas
- Generates workflows directly (validated by the scheduler before execution)
- Receives session handoff when context approaches the model's window limit

## What the Overseer Is NOT

- Not an executor. It never runs workflows, invokes agent steps, or manages state. That's the scheduler.
- Not a worker. It never writes files, runs tests, or produces code. That's agent steps.
- Not a router. It doesn't pick models or resolve categories. That's agent/ and the scheduler.

## Tools

Read-only inspection tools only. The Overseer can look at anything but touch nothing.

| Tool | Purpose |
|---|---|
| `read` | Read file contents |
| `grep` | Search file contents |
| `glob` | Find files by pattern |

No write, edit, bash, spawn, consult, or notepad. The Overseer's influence on the world is entirely through its structured decision outputs, which the scheduler acts on.

## Decision Protocol Registry

Each decision type is a registered protocol with a fixed structure:

| Protocol | When it fires | Output (closed menu) |
|---|---|---|
| `intent_decision` | Start of run | Intent description, initial constraints, initial assumptions |
| `workflow_decision` | Overseer determines work is needed | Pipeline TOML definition (validated before execution) |
| `retry_decision` | Step or workflow fails | Retry with same context / retry with wider context / re-decompose / defer to human |
| `budget_decision` | Budget threshold crossed or workflow exhausted | Reallocate from completed workflows / increase allocation / pause / abort |
| `escalation_decision` | Agent step suggests modification or unknown situation | Accept suggestion / reject / escalate to human |
| `assumption_decision` | Information needed, human not available | Make assumption (recorded) / escalate to human inbox |
| `concurrency_decision` | Multiple workflows or iterations could run in parallel | Degree of parallelism, which workflows to parallelize |
| `constraint_decision` | New constraint discovered during execution | Add mechanical constraint / add advisory constraint / ignore |
| `scope_decision` | Agent step discovers it needs resources outside its scope | Approve expansion / deny / spawn child workflow |
| `context_decision` | Before each agent step | Refine the mechanically assembled context: select lessons, request additional code context, reorder/trim layers, or accept as-is. Both pre- and post-refinement versions are stored for learning. |
| `audit_decision` | Overseer suspects quality issues | Spawn audit workflow (read-only, real-time or post-hoc) / skip |

Each protocol defines:
- A system prompt template (fixed per type, with slots for assembled context)
- An input schema (what the scheduler provides)
- An output schema (closed set of actions -- the Overseer picks from a menu)
- A context assembly rule (which SQLite tables/fields to query for this decision type)

If a situation does not match any registered protocol, that itself is an escalation to the human.

## Memory: SQLite Database

A structured, queryable store. Not a growing document.

| Table | Columns | Notes |
|---|---|---|
| `decisions` | id, type, timestamp, choice, rationale, outcome | outcome updated when known |
| `constraints` | text, source_decision_id, active, tier | tier is `mechanical` or `advisory`. active is boolean |
| `assumptions` | text, status, scope, dependent_workflows | status: pending / confirmed / contradicted. scope: understanding / decomposition / task |
| `lessons` | text, timestamp, relevance_tags | |
| `workflow_status` | workflow_id, current_step, health, last_updated | overwritten, not appended |

Context assembly queries this database per decision type:
- Retry decision gets: active constraints + failing workflow's status + last 3 retry decisions + relevant lessons + error classification
- Budget decision gets: all workflow statuses + budget history + last 3 budget decisions
- Each query returns bounded, relevant context. The database grows but context per call does not.

Each decision can declare `constrains_future` in its output -- a list of constraint strings written to the constraints table with `source_decision_id`. All subsequent calls include active constraints. Constraint accumulation is explicit, auditable, and traceable.

## Constraint Tiers

Constraints are either mechanical (enforced by the scheduler after each step) or advisory (included in agent/overseer context as guidance).

Mechanical constraints use a closed vocabulary of checkable primitives:
- `tests_pass` -- test suite must pass (always implicitly active)
- `lint_clean` -- linter must pass (always implicitly active)
- `no_removed_exports(glob)` -- public API symbols cannot be removed from matching files
- `no_changed_signatures(glob)` -- function/method signatures cannot change
- `no_new_dependencies` -- no additions to dependency manifests
- `no_new_files_outside(directory)` -- no file creation outside specified directory

The scheduler checks mechanical constraints after each step's verification chain. Violations are immediate failures.

Advisory constraints are freeform text included in agent step context and Overseer calls. Not mechanically enforced.

## Sanity-Check Subagents

After every Overseer decision, before the scheduler executes it, independent cheap LLM calls check:
- **Constraint consistency**: does this decision contradict any active constraint?
- **Repetition**: is this the same action for the same failure as last time?
- **Proportionality** (for budget/concurrency): is this allocation proportional to remaining work?

Narrow, structured, tiny context, fast model. They catch self-contradiction, repeating failed strategies, and disproportionate allocation.

## Health Monitoring

The scheduler tracks Overseer health metrics:
- Parse failure rate (output did not match protocol schema)
- Contradiction rate (sanity check flagged constraint violation)
- Repetition rate (sanity check flagged same-action-on-same-failure)

If any rate exceeds threshold over a rolling window, the scheduler enters degraded mode for that specific decision type:

| Decision Type | Degraded Behavior |
|---|---|
| Retry | Fixed escalation ladder: same context, then wider context, then mark failed |
| Budget | Maintain current allocations |
| Escalation | Escalate everything to human |
| Concurrency | Serialize everything |

The Overseer is not disabled -- it is bypassed for the failing decision type only. Other types continue normally. If rates recover, degraded mode exits automatically.

Overseer calls are funded from the triggering workflow's budget. If workflow A fails and triggers a `retry_decision`, that call's tokens come from workflow A's budget.

## Session Handoff

The Overseer is the only entity that receives session handoff. Agent steps are scoped and short-lived -- if an agent step can't finish within its context window, that's a decomposition problem, not a compaction problem.

When the Overseer's conversation approaches ~90% of the model's context window:

1. The scheduler detects the threshold (it tracks token usage).
2. The scheduler asks the Overseer to produce a detailed summary of the run so far.
3. The scheduler persists the full transcript via `trace/`.
4. The scheduler creates a new Overseer session with the summary as initial context plus the old session's UUID for querying the full transcript.

The new session has both a summary for quick reference and the full record for deep lookups via trace queries.

## Audit Workflows

When the Overseer suspects quality issues (via `audit_decision`), it can spawn an audit workflow. Audit workflows are regular workflows with `write_paths = []` -- read-only, no mutations. They have full structured visibility into the system's state via trace/ queries, including in-progress work.

Audits can run at any time:
- **Concurrent with in-progress work** -- the audit reads the current state of files, workflow progress, and agent outputs as they happen. Useful for catching problems early.
- **Post-hoc on completed work** -- the audit reviews the final output of a completed workflow. The standard quality review.

The Overseer reviews the audit workflow's findings and decides what to do: spawn remediation workflows, adjust constraints, escalate to human, or accept the work.

## Coherence Summary

At the end of a run, the Overseer reviews the full accumulated diff against the original intent. It scores whether the changes accomplish what was asked, flags gaps between intent and result, and notes unexpected side effects. The coherence summary is written to the report via trace/.

## Human Inbox

Structured async queue. The system never blocks waiting for human input.

Each inbox item has:
- The question
- Options considered
- Which option the Overseer assumed (via `assumption_decision`)
- What work proceeds under that assumption
- What happens if the human picks differently
- A deadline

Assumptions are never rewound. If a human response contradicts an assumption, the contradiction is flagged in the report. The work stands. The human decides whether to re-run.

Assumptions are tagged with scope (understanding / decomposition / task), determined mechanically by which pipeline stage is running when the assumption is made.

## Autonomy Knob

Single scalar. Mechanical action-type rules, not Overseer judgment.

| Level | Overseer handles autonomously |
|---|---|
| Low | Read-only decisions only. Almost everything goes to human inbox. |
| Medium | Retry, budget reallocation, concurrency, task-level assumptions. Scope changes, architecture decisions, understanding-level assumptions escalate. |
| High | Everything except: modifying external API contracts, changing auth/security flows, deleting data, deploying, adding new external dependencies. |
| Max | Everything. Human inbox empty. Report is audit trail. |

Each level maps to an explicit list of action types that are autonomous vs escalated. The mapping is published and deterministic. Can change mid-run.

## Error Taxonomy

The scheduler classifies every failure before escalating to the Overseer:

| Category | Pattern | Example |
|---|---|---|
| infra | Timeout, network error, disk full, OOM | ETIMEDOUT, No space left |
| parse | LLM output did not match schema | JSON parse error, missing field |
| flaky | Non-deterministic test failure | Test passed on re-run without changes |
| build_env | Missing dependency, wrong version | ModuleNotFoundError |
| logic | Consistent test failure from code error | AssertionError |
| unclassified | No pattern matched | |

The Overseer sees the classification and applies type-appropriate strategies. The scheduler does not auto-handle any category.

## Cross-Run Learning

Per-project knowledge base persisted to disk:
- Architecture patterns, failure patterns, flaky tests, conventions, environment requirements
- Entries have timestamps and source file paths
- The scheduler checks via git whether source files have changed and flags stale entries
- Entries expire after N runs unless explicitly confirmed by a human
- If the Overseer encounters contradicting evidence, it marks the entry as disputed and escalates
- Permanent entries are human-curated, not system-generated

The Overseer reads the knowledge base at the start of every run.

### Consumer Knowledge Files

Consumers can provide domain-specific knowledge as files in a known directory (`knowledge/` alongside agents and pipelines). Two formats:

**Markdown files** (`.md`) -- free-form domain knowledge: coding conventions, architectural constraints, banned patterns, framework-specific guidance. Each file is loaded as a permanent entry in the knowledge base. Files can contain front matter for metadata:

```markdown
---
tags: [code-quality, determinism]
---
Never use Math.random() or Date.now() in generated code. These break deterministic replay.
Use the framework's seeded RNG and tick-based timers instead.
```

**TOML files** (`.toml`) -- structured constraints that map to the Overseer's constraint system:

```toml
[[constraints]]
text = "All generated code must pass lint and type checks before commit"
tier = "mechanical"

[[constraints]]
text = "Prefer composition over inheritance in generated components"
tier = "advisory"
```

Consumer knowledge files are loaded at the start of every run. They are permanent -- they do not expire and are not subject to staleness detection. The Overseer treats them as authoritative domain knowledge, equivalent to entries confirmed by a human.

### Permanent Knowledge Base Entries

The SQLite knowledge base supports two entry lifetimes:

**Transient entries** (the default) -- generated by the system during runs. Subject to staleness detection via git, expiry after N runs, and human confirmation prompts. These represent things the system learned.

**Permanent entries** -- loaded from consumer knowledge files or explicitly confirmed by a human. Never expire, never flagged as stale, never prompted for confirmation. These represent things the consumer knows to be true about their domain.

The `lessons` table gains a `permanent` boolean column (default false). Consumer knowledge files set `permanent = true`. The Overseer can propose promoting a transient entry to permanent (via escalation to human), but cannot do so autonomously.

## Files

| File | Contents |
|---|---|
| `_overseer.py` | `Overseer` class. Manages the persistent session, assembles context per decision type, parses structured outputs, records decisions to SQLite. |
| `_protocols.py` | Decision protocol registry. Each protocol: system prompt template, input schema, output schema, context assembly rule. |
| `_memory.py` | SQLite database management. Tables: decisions, constraints, assumptions, lessons, workflow_status. Context assembly queries. |
| `_health.py` | Health monitoring. Tracks parse failure rate, contradiction rate, repetition rate. Degraded mode logic per decision type. |
| `_handoff.py` | Session handoff detection and execution. Moved from session/ -- applies only to the Overseer. |
| `_inbox.py` | Human inbox. Structured async queue for escalations and assumptions. |
| `_autonomy.py` | Autonomy knob. Level definitions, action-type-to-level mapping, escalation routing. |
| `_errors.py` | Error taxonomy. Classification logic: exit codes, stderr patterns, error messages to category. |
| `_learning.py` | Cross-run knowledge base. Persistence, staleness detection, expiry. |
| `_knowledge.py` | Consumer knowledge file loading. Reads .md and .toml files from the knowledge directory, parses front matter, creates permanent entries. |
| `_sanity.py` | Sanity-check subagent dispatch. Constraint consistency, repetition, proportionality checks. |

## What This Module Does NOT Do

- Does not execute workflows or agent steps (that's scheduler/)
- Does not write files, run commands, or produce code
- Does not validate workflow TOML schema (that's scheduler/)
- Does not manage transport connections or sessions (that's session/)
- Does not know about specific pipeline templates (build/debug/review are consumer domain)
