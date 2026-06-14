# Trace Module Design

Single owner of the PostgreSQL schema. All persistent state flows through this module. No other module writes to the database directly.

## Responsibility

Define and manage the database schema. Write and read all run artifacts: events, step results (per-attempt), transport event logs, session transcripts, inbox items, notepad entries, config snapshots, workflow/step state machines. Enable crash recovery, session handoff, and cross-process observation.

## Database Schema

The trace module owns every table. All primary keys are UUIDv7 (time-ordered, via the `uuid6` package). Timestamps are `timestamptz`.

### Tables

#### `runs`

Top-level run records.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | UUIDv7 |
| `intent` | text NOT NULL | The user's original intent |
| `status` | text NOT NULL | `created`, `running`, `paused`, `completed`, `failed`, `aborted` |
| `autonomy_level` | text NOT NULL | The autonomy setting at run start |
| `config_snapshot` | jsonb NOT NULL | Fully resolved configuration at run start: categories, budgets, tool registry names, agent definitions |
| `started_at` | timestamptz | |
| `finished_at` | timestamptz | |
| `total_input_tokens` | bigint DEFAULT 0 | |
| `total_output_tokens` | bigint DEFAULT 0 | |
| `total_reasoning_tokens` | bigint DEFAULT 0 | |
| `total_cost_usd` | numeric DEFAULT 0 | Best-effort, from oxtra's internal pricing table |
| `coherence_summary` | text | Overseer's end-of-run assessment |

#### `workflows`

Workflow instances within a run.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | uuid FK -> runs | |
| `parent_workflow_id` | uuid FK -> workflows, nullable | For child workflows |
| `name` | text NOT NULL | |
| `description` | text | |
| `status` | text NOT NULL | `created`, `running`, `paused`, `completed`, `failed`, `aborted` |
| `budget_usd` | numeric | Per-workflow budget set by the Overseer |
| `spent_usd` | numeric DEFAULT 0 | |
| `created_at` | timestamptz | |
| `finished_at` | timestamptz | |

#### `steps`

Step definitions within a workflow.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `workflow_id` | uuid FK -> workflows | |
| `name` | text NOT NULL | |
| `step_type` | text NOT NULL | `agent`, `callable`, `workflow`, `decision_point`, `gate` |
| `status` | text NOT NULL | `pending`, `ready`, `running`, `completed`, `failed`, `skipped` |
| `agent_name` | text | For agent steps |
| `task_template` | text | For agent steps |
| `callable_path` | text | For callable steps |
| `config` | jsonb NOT NULL | Full step configuration from TOML (timeout, retry, verify, etc.) |
| `created_at` | timestamptz | |
| `finished_at` | timestamptz | |

#### `step_attempts`

Per-attempt results. Retries never overwrite history.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `step_id` | uuid FK -> steps | |
| `attempt` | smallint NOT NULL | 1-indexed |
| `status` | text NOT NULL | `running`, `completed`, `failed` |
| `agent_output` | text | Full text output from the agent |
| `structured_output` | jsonb | Validated JSON if output_schema was set |
| `verify_result` | jsonb | Mechanical verification result |
| `verify_verdict` | jsonb | Semantic verification verdict (structured) |
| `session_id` | text | Transport session ID |
| `input_tokens` | bigint DEFAULT 0 | |
| `output_tokens` | bigint DEFAULT 0 | |
| `reasoning_tokens` | bigint DEFAULT 0 | |
| `cost_usd` | numeric DEFAULT 0 | |
| `duration_seconds` | numeric | |
| `started_at` | timestamptz | |
| `finished_at` | timestamptz | |

UNIQUE constraint: `(step_id, attempt)`.

#### `events`

Append-only audit log. Immutable by design.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | UUIDv7 |
| `run_id` | uuid FK -> runs | |
| `workflow_id` | uuid FK -> workflows, nullable | |
| `step_id` | uuid FK -> steps, nullable | |
| `event_type` | text NOT NULL | |
| `data` | jsonb DEFAULT '{}' | Event-specific payload |
| `created_at` | timestamptz DEFAULT now() | |

**REVOKE UPDATE, DELETE** on this table at the DB-role level.

Indexes: `(run_id, created_at DESC)`, `(workflow_id, created_at DESC)`, `(step_id, created_at DESC)`, `(event_type, created_at DESC)`.

**LISTEN/NOTIFY**: every INSERT fires a NOTIFY on channel `oxtra_events` with `{event_id, run_id, event_type}` as payload. Full event data stays in the table -- NOTIFY carries only routing IDs to avoid the 8KB payload limit.

Event types include:
- `run.started`, `run.completed`, `run.failed`, `run.aborted`
- `workflow.created`, `workflow.status_changed`
- `step.status_changed`, `step.attempt_started`, `step.attempt_completed`, `step.attempt_failed`
- `inbox.item_created`, `inbox.item_answered`, `inbox.item_skipped`, `inbox.item_expired`
- `overseer.decision`, `overseer.handoff`
- `budget.threshold_crossed`, `budget.exhausted`

State-change events carry `{old_status, new_status, reason}`.

#### `transcripts`

Full conversation history per session. Append-only.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `session_id` | text NOT NULL | Transport session ID |
| `run_id` | uuid FK -> runs | |
| `turn` | integer NOT NULL | |
| `role` | text NOT NULL | `user`, `assistant`, `tool_result` |
| `content` | text NOT NULL | Full content (never truncated) |
| `tool_calls` | jsonb | For assistant turns: `[{name, input, output}]` |
| `input_tokens` | bigint | Per-turn |
| `output_tokens` | bigint | Per-turn |
| `created_at` | timestamptz | |

**REVOKE UPDATE, DELETE** on this table.

#### `notepad_entries`

Cross-agent context sharing. Append-only.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | uuid FK -> runs | |
| `step_name` | text NOT NULL | |
| `agent_name` | text NOT NULL | |
| `entry_type` | text NOT NULL | `learning`, `decision`, `issue` |
| `text` | text NOT NULL | |
| `created_at` | timestamptz DEFAULT now() | |

**REVOKE UPDATE, DELETE** on this table.

#### `inbox_items`

Human inbox. See root DESIGN.md for the full inbox design.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | uuid FK -> runs | |
| `protocol_type` | text NOT NULL | Decision protocol that emitted this (auto-injected tag) |
| `question` | text NOT NULL | |
| `options` | jsonb NOT NULL | Options considered |
| `assumed_option` | text NOT NULL | Which option the Overseer assumed |
| `work_proceeding` | text NOT NULL | What work continues under the assumption |
| `contradiction_impact` | text NOT NULL | What happens if the human picks differently |
| `tags` | text[] DEFAULT '{}' | Free-form tags (framework auto-injects protocol_type; Overseer adds extras) |
| `status` | text NOT NULL DEFAULT 'pending' | `pending`, `answered`, `skipped`, `expired` |
| `answer` | text | Human's response |
| `deadline` | timestamptz | |
| `answer_event` | text | Event name fired when answered (for gate steps to await) |
| `created_at` | timestamptz DEFAULT now() | |
| `answered_at` | timestamptz | |

Index: `(run_id, status, created_at)` WHERE status = 'pending'.

#### `context_diffs`

Pre- and post-Overseer-refinement context per agent step.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `step_attempt_id` | uuid FK -> step_attempts | |
| `pre_refinement` | text NOT NULL | Mechanically assembled context |
| `post_refinement` | text NOT NULL | Overseer-refined context |
| `created_at` | timestamptz | |

### Overseer Memory Tables

These tables are owned by the trace module but primarily read/written by the Overseer module.

#### `decisions`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | uuid FK -> runs | |
| `protocol_type` | text NOT NULL | Decision protocol name |
| `choice` | jsonb NOT NULL | Structured output from the protocol |
| `rationale` | text | |
| `outcome` | text | Updated when outcome is known |
| `created_at` | timestamptz | |

#### `constraints`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | uuid FK -> runs | |
| `text` | text NOT NULL | |
| `source_decision_id` | uuid FK -> decisions | |
| `tier` | text NOT NULL | `mechanical` or `advisory` |
| `active` | boolean DEFAULT true | |
| `created_at` | timestamptz | |

#### `assumptions`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `run_id` | uuid FK -> runs | |
| `inbox_item_id` | uuid FK -> inbox_items, nullable | |
| `text` | text NOT NULL | |
| `status` | text NOT NULL | `pending`, `confirmed`, `contradicted` |
| `scope` | text NOT NULL | `understanding`, `decomposition`, `task` |
| `created_at` | timestamptz | |

#### `workflow_status`

Overseer's view of workflow health. Overwritten, not appended.

| Column | Type | Notes |
|---|---|---|
| `workflow_id` | uuid PK, FK -> workflows | |
| `current_step` | text | |
| `health` | text NOT NULL | `healthy`, `degraded`, `failing` |
| `last_updated` | timestamptz | |

### State Machine Transitions

Legal transitions are enumerated and enforced by the trace module. Invalid transitions are hard errors. Every transition emits an event with `{old_status, new_status, reason}`.

**Run statuses:**
- `created` -> `running`
- `running` -> `paused`, `completed`, `failed`, `aborted`
- `paused` -> `running`, `aborted`

**Workflow statuses:**
- `created` -> `running`
- `running` -> `paused`, `completed`, `failed`, `aborted`
- `paused` -> `running`, `aborted`

**Step statuses:**
- `pending` -> `ready`
- `ready` -> `running`, `skipped`
- `running` -> `completed`, `failed`
- `failed` -> `ready` (retry)

**Step attempt statuses:**
- `running` -> `completed`, `failed`

### Mutual Exclusion

The scheduler acquires a PostgreSQL advisory lock per run at startup. A second scheduler on the same run is a hard error. The lock includes a heartbeat: the scheduler periodically updates a heartbeat timestamp. Stale locks (dead PID, heartbeat older than threshold) are reclaimed during the recovery sequence.

## Write API

```python
class TraceWriter:
    async def create_run(self, intent: str, config: dict, autonomy_level: str) -> uuid.UUID: ...
    async def transition_run(self, run_id, new_status, reason): ...
    async def create_workflow(self, run_id, name, description, parent_id=None, budget_usd=None) -> uuid.UUID: ...
    async def transition_workflow(self, workflow_id, new_status, reason): ...
    async def create_step(self, workflow_id, name, step_type, config) -> uuid.UUID: ...
    async def transition_step(self, step_id, new_status, reason): ...
    async def create_step_attempt(self, step_id, attempt) -> uuid.UUID: ...
    async def complete_step_attempt(self, attempt_id, output, structured_output, verify_result, verify_verdict, session_id, tokens, cost, duration): ...
    async def fail_step_attempt(self, attempt_id, error, session_id, tokens, cost, duration): ...
    async def write_event(self, run_id, event_type, data, workflow_id=None, step_id=None): ...
    async def write_transcript_entry(self, session_id, run_id, turn, role, content, tool_calls=None, tokens=None): ...
    async def write_notepad_entry(self, run_id, step_name, agent_name, entry_type, text): ...
    async def create_inbox_item(self, run_id, protocol_type, question, options, assumed_option, work_proceeding, contradiction_impact, tags=None, deadline=None, answer_event=None) -> uuid.UUID: ...
    async def answer_inbox_item(self, item_id, answer): ...
    async def skip_inbox_item(self, item_id): ...
    async def expire_inbox_item(self, item_id): ...
    async def write_context_diff(self, attempt_id, pre, post): ...
    async def write_decision(self, run_id, protocol_type, choice, rationale=None) -> uuid.UUID: ...
    async def write_constraint(self, run_id, text, source_decision_id, tier): ...
    async def write_assumption(self, run_id, text, scope, inbox_item_id=None): ...
    async def update_workflow_status(self, workflow_id, current_step, health): ...
```

All state-changing methods enforce legal transitions and emit events within the same transaction. LISTEN/NOTIFY fires inside the transaction.

## Read API

```python
async def read_step_attempt(pool, step_id: uuid.UUID, attempt: int) -> StepAttempt | None: ...
async def read_latest_attempt(pool, step_id: uuid.UUID) -> StepAttempt | None: ...
async def read_transcript(pool, session_id: str) -> list[dict]: ...
async def query_transcript(pool, session_id: str, query: str) -> list[dict]: ...
async def read_run_report(pool, run_id: uuid.UUID) -> RunReport | None: ...
async def list_runs(pool) -> list[RunSummary]: ...
async def read_inbox(pool, run_id: uuid.UUID, status: str | None = None) -> list[InboxItem]: ...
async def read_notepad(pool, run_id: uuid.UUID) -> list[NotepadEntry]: ...
async def format_notepad(entries: list[NotepadEntry]) -> str: ...
```

## Run Report

Generated at the end of a run from database queries:

- Outcome: passed, intent, coherence summary, duration
- Steps: per-step status ordered by execution, per-attempt details
- Workflows: all workflows spawned, their budgets and spend
- Tokens: per-type totals (input, output, reasoning, cache_read, cache_write)
- Cost: total USD (best-effort)
- Decisions: from the decisions table
- Constraints and assumptions: active at end of run
- Overseer health: parse failure rate, contradiction rate, repetition rate
- Error breakdown: category counts
- Context refinement: number of steps where the Overseer refined context

## Crash Recovery

The trace module enables crash recovery by persisting step attempts incrementally. On restart, the recovery sequence (three passes):

1. **Reclaim interrupted work**: find steps/attempts in `running` status, transition them to `failed` with reason "crash recovery"
2. **Re-evaluate blocked work**: find steps in `pending`/`ready` whose dependencies may now be met; re-check gate conditions
3. **Clean orphaned resources**: release stale advisory locks (dead heartbeat), stop orphaned services, release stale file locks

Each pass is idempotent -- safe to re-run after another crash.

## Session Handoff Support

When the scheduler detects the Overseer's context approaching 90% of the model's window:

1. The scheduler asks the Overseer to produce a summary
2. The scheduler ensures the transcript is fully persisted (already guaranteed by PG writes)
3. A new Overseer session is created with the summary as initial context
4. The new session receives the old session's UUID for querying the full transcript

## Live Event Stream

Two mechanisms at different process boundaries:

- **In-process callback**: `TraceWriter` accepts an optional async callable, invoked with each event dict as it's written. Push-style, one hook point. For the scheduler and in-process consumers.
- **Cross-process NOTIFY**: every event INSERT fires NOTIFY on `oxtra_events`. The MCP server and dashboard subscribe via LISTEN. NOTIFY carries only `{event_id, run_id, event_type}` -- full data read from the table.

These are not dual paths -- they serve different boundaries. The callback is the library-level hook; NOTIFY is the network-level channel.

## Files

| File | Contents |
|---|---|
| `_types.py` | `RunReport`, `StepAttempt`, `RunSummary`, `InboxItem`, `NotepadEntry`, `WorkflowSummary`, `DecisionSummary`, `ConstraintSummary`, `AssumptionSummary`, `HealthSummary` frozen dataclasses. Pydantic models for validation. |
| `_schema.py` | All CREATE TABLE statements. Schema migration support. REVOKE statements for immutable tables. Index definitions. |
| `_writer.py` | `TraceWriter` class. Bound to an asyncpg pool. All write methods with transaction management, state machine enforcement, and NOTIFY dispatch. |
| `_reader.py` | Read API: `read_step_attempt()`, `read_transcript()`, `query_transcript()`, `read_run_report()`, `list_runs()`, `read_inbox()`, `read_notepad()`, `format_notepad()`. |
| `_transitions.py` | State machine definitions: legal transitions per entity type. `transition(entity, old_status, new_status)` validates and raises on illegal transitions. |
| `_recovery.py` | Three-pass crash recovery: reclaim interrupted work, re-evaluate blocked work, clean orphaned resources. |
| `_lock.py` | Advisory lock acquisition, heartbeat renewal, stale lock detection and reclamation. |

## What This Module Does NOT Do

- Does not decide when to write (that's scheduler/ and session/)
- Does not decide what to verify (that's verify/)
- Does not enforce retention policies or disk limits
- Does not implement the session handoff decision logic (that's the Overseer via the scheduler)
- Does not own the run directory on the filesystem (all state is in PG)
