# Overseer Module Design

The Overseer is the brain of the system. One persistent agent with read-only tools, a long memory, and structured decision protocols. It observes everything, decides what should happen, and orders short-lived agent steps to do the work. It never mutates files, runs commands, or executes tasks directly.

## Responsibility

Drive intent to completion. The Overseer receives the user's intent at the start of a run and makes every judgment call needed to fulfill it: what to do, how to decompose it, when to retry, when to escalate, how to allocate budget, what assumptions to make. It produces workflows (TOML definitions) that the scheduler validates and executes. It monitors execution and reacts to events (step failures, budget thresholds, human responses).

## What the Overseer Is

- One agent with persistent context across the entire run
- Has read-only tools (read, grep, glob -- inspection, not mutation)
- Has structured memory (PostgreSQL tables via the trace module)
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
| `workflow_decision` | Overseer determines work is needed | Workflow TOML definition (validated before execution) |
| `retry_decision` | Step or workflow fails | Retry with same context / retry with wider context / re-decompose / defer to human |
| `budget_decision` | Budget threshold crossed or workflow exhausted | Reallocate from completed workflows / increase allocation / pause / abort |
| `escalation_decision` | Agent step suggests modification or unknown situation | Accept suggestion / reject / escalate to human |
| `assumption_decision` | Information needed, human not available | Make assumption (recorded) / escalate to human inbox |
| `concurrency_decision` | Multiple workflows or iterations could run in parallel | Degree of parallelism, which workflows to parallelize |
| `constraint_decision` | New constraint discovered during execution | Add mechanical constraint / add advisory constraint / ignore |
| `scope_decision` | Agent step needs resources outside its scope | Approve expansion / deny / spawn child workflow |
| `context_decision` | Before each agent step | Refine the mechanically assembled context: select lessons, request additional code context, reorder/trim layers, or accept as-is |
| `audit_decision` | Overseer suspects quality issues | Spawn audit workflow (read-only) / skip |

Each protocol defines:
- A system prompt template (fixed per type, with slots for assembled context)
- An input schema (what the scheduler provides)
- An output schema (closed set of actions -- the Overseer picks from a menu)
- A context assembly rule (which PG tables/fields to query for this decision type)

If a situation does not match any registered protocol, that itself is an escalation to the human via `protocol_gap` inbox item.

## Memory: PostgreSQL Tables

Structured, queryable store. Tables owned by the trace module, read/written by the Overseer.

| Table | Key Columns | Notes |
|---|---|---|
| `decisions` | id, run_id, protocol_type, choice, rationale, outcome | outcome updated when known |
| `constraints` | text, source_decision_id, active, tier | tier is `mechanical` or `advisory` |
| `assumptions` | text, status, scope, inbox_item_id | status: pending / confirmed / contradicted. scope: understanding / decomposition / task |
| `lessons` | text, relevance_tags, permanent, source_file | Primary store for learned facts. The knowledge module indexes these into cognee for semantic retrieval. |
| `workflow_status` | workflow_id, current_step, health | overwritten, not appended |

Context assembly queries this database per decision type:
- Retry decision gets: active constraints + failing workflow's status + last 3 retry decisions + relevant lessons + error classification
- Budget decision gets: all workflow statuses + budget history + last 3 budget decisions
- Each query returns bounded, relevant context. The database grows but context per call does not.

Each decision can declare `constrains_future` in its output -- a list of constraint strings written to the constraints table. Constraint accumulation is explicit, auditable, and traceable.

## Constraint Tiers

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

Overseer calls are funded from the triggering workflow's budget.

## Session Handoff

The Overseer is the only entity that receives session handoff. Agent steps are scoped and short-lived.

When the Overseer's conversation approaches ~90% of the model's context window:

1. The scheduler detects the threshold (it tracks token usage).
2. The scheduler asks the Overseer to produce a detailed summary.
3. The transcript is already fully persisted in PG.
4. The scheduler creates a new Overseer session with the summary as initial context plus the old session's UUID for querying the full transcript.

## Audit Workflows

When the Overseer suspects quality issues (via `audit_decision`), it can spawn an audit workflow. Audit workflows are regular workflows with `write_paths = []` -- read-only, no mutations.

Audits can run concurrent with in-progress work or post-hoc on completed work. The Overseer reviews audit findings and decides: spawn remediation workflows, adjust constraints, escalate to human, or accept.

## Coherence Summary

At the end of a run, the Overseer reviews the full accumulated diff against the original intent. It scores whether the changes accomplish what was asked, flags gaps, and notes unexpected side effects. Written to the run's `coherence_summary` field.

## Autonomy Knob

Single scalar. Mechanical action-type rules, not Overseer judgment.

| Level | Overseer handles autonomously |
|---|---|
| Low | Read-only decisions only. Almost everything goes to human inbox. |
| Medium | Retry, budget reallocation, concurrency, task-level assumptions. Scope changes, architecture decisions, understanding-level assumptions escalate. |
| High | Everything except: modifying external API contracts, changing auth/security flows, deleting data, deploying, adding new external dependencies. |
| Max | Everything. Human inbox empty. Report is audit trail. |

Each level maps to an explicit list of action types that are autonomous vs escalated. The mapping is published and deterministic. Can change mid-run.

**Action-gating**: irreversible actions (deploy, delete data, external sends) are forbidden below the configured autonomy level by the scheduler's action-type mapping. They require an answered approval inbox item to proceed. This is one of the three explicit blocking mechanisms (see root DESIGN.md).

## Error Taxonomy

The scheduler classifies every failure before escalating to the Overseer:

| Category | Pattern | Example |
|---|---|---|
| infra | Timeout, network error, disk full, OOM, transient API errors exhausted | ETIMEDOUT, No space left, 429 after retries |
| parse | LLM output did not match schema | JSON parse error, missing field |
| flaky | Non-deterministic test failure | Test passed on re-run without changes |
| build_env | Missing dependency, wrong version | ModuleNotFoundError |
| logic | Consistent test failure from code error | AssertionError |
| unclassified | No pattern matched | |

The Overseer sees the classification and applies type-appropriate strategies.

## Cross-Run Learning

The `lessons` table (owned by trace/) is the primary store for cross-run knowledge:
- Architecture patterns, failure patterns, flaky tests, conventions, environment requirements
- Entries have timestamps and source file paths
- The scheduler checks via git whether source files have changed and flags stale entries
- Entries expire after N runs unless explicitly confirmed by a human
- If the Overseer encounters contradicting evidence, it marks the entry as disputed and escalates
- Permanent entries are human-curated or loaded from consumer knowledge files

The Overseer reads the lessons table at the start of every run. Context assembly queries it with flat SQL filtered by relevance tags.

The `knowledge/` module (experimental) additionally indexes lessons into a cognee knowledge graph for semantic retrieval. When enabled, context assembly receives results from both flat SQL and cognee's graph traversal. See `knowledge/DESIGN.md`.

### Consumer Knowledge Files

Consumers provide domain-specific knowledge as files in a `knowledge/` directory. Two formats:

**Markdown files** (`.md`) -- free-form domain knowledge. Written to the lessons table as permanent entries. Optionally indexed into cognee when the knowledge module is enabled.

**TOML files** (`.toml`) -- structured constraints that map to the constraint system:

```toml
[[constraints]]
text = "All generated code must pass lint and type checks before commit"
tier = "mechanical"

[[constraints]]
text = "Prefer composition over inheritance in generated components"
tier = "advisory"
```

Consumer knowledge files are loaded at the start of every run. They are permanent -- they do not expire and are not subject to staleness detection.

## Files

| File | Contents |
|---|---|
| `_overseer.py` | `Overseer` class. Manages the persistent session, assembles context per decision type, parses structured outputs, records decisions via trace. |
| `_protocols.py` | Decision protocol registry. Each protocol: system prompt template, input schema, output schema, context assembly rule. All schemas are pydantic models with `strict=True, extra='forbid'`. |
| `_memory.py` | Context assembly queries against PG tables (decisions, constraints, assumptions, lessons, workflow_status). Also calls knowledge module retrieval for semantic graph results when available. |
| `_health.py` | Health monitoring. Tracks parse failure rate, contradiction rate, repetition rate. Degraded mode logic per decision type. |
| `_handoff.py` | Session handoff detection and execution. |
| `_inbox.py` | Human inbox item creation. Structured async queue for escalations and assumptions. |
| `_autonomy.py` | Autonomy knob. Level definitions, action-type-to-level mapping, escalation routing, action-gating enforcement. |
| `_errors.py` | Error taxonomy. Classification logic: exit codes, stderr patterns, error messages to category. |
| `_learning.py` | Cross-run knowledge base queries against the lessons table. Staleness detection via git, expiry after N runs. |
| `_knowledge.py` | Consumer knowledge file loading. Reads .md and .toml files from the knowledge directory, writes to lessons table and constraints table via trace. |
| `_sanity.py` | Sanity-check subagent dispatch. Constraint consistency, repetition, proportionality checks. |

## What This Module Does NOT Do

- Does not execute workflows or agent steps (that's scheduler/)
- Does not write files, run commands, or produce code
- Does not validate workflow TOML schema (that's scheduler/)
- Does not manage transport connections or sessions (that's session/)
- Does not know about specific workflow templates (those are consumer domain)
