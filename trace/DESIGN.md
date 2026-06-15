# Trace Module Design

Single owner of the PostgreSQL schema. All persistent state flows through this module. No other module writes to the database directly.

## Responsibility

Define and manage the database schema. Write and read all run artifacts: events, task results (per-attempt), session transcripts, inbox items, notepad entries, config snapshots, task state machines. Enable crash recovery, session handoff, and cross-process observation.

## Database Schema

The trace module owns every table. All primary keys are UUIDv7 (via the pg-uuidv7 PostgreSQL extension -- no v4 fallback). Timestamps are `timestamptz`.

The schema will be redesigned during implementation to support the unified task model (tasks table replacing separate workflows/steps). The current schema (in `schema/orxt.toml`) is the design-phase version.

### Key Tables

#### `runs`

Top-level run records. Columns: id, intent, status, autonomy_level, config_snapshot, started_at, finished_at, total_input_tokens, total_output_tokens, total_reasoning_tokens, total_cache_read_tokens, total_cache_write_tokens, total_cost_usd, coherence_summary.

#### `tasks` (to be redesigned)

Currently split as `workflows` and `steps`. Will be unified into a single `tasks` table with `parent_task_id` for nesting during implementation.

#### `step_attempts` (to be renamed `task_attempts`)

Per-attempt results. Columns include: agent_output, structured_output, verify_result, verify_verdict (to be renamed check_result, check_verdict), session_id, input_tokens, output_tokens, reasoning_tokens, cache_read_tokens, cache_write_tokens, cost_usd, duration_seconds.

UNIQUE constraint: `(task_id, attempt)`.

#### `events`

Append-only audit log. `REVOKE UPDATE, DELETE`. Indexes on `(run_id, created_at DESC)`, `(event_type, created_at DESC)`.

LISTEN/NOTIFY: a trigger function on INSERT issues `NOTIFY orxt_events` with `{event_id, run_id, event_type}` as payload.

#### `transcripts`

Full conversation history per session. Append-only. `REVOKE UPDATE, DELETE`.

#### `notepad_entries`

Cross-agent context sharing. Append-only. `REVOKE UPDATE, DELETE`.

#### `inbox_items`

Human inbox. Five-status lifecycle: pending, answered, skipped, expired, rejected. Rejected items include a `rejection_reason` and trigger Overseer re-investigation.

#### `context_diffs`

Pre-refinement context (full text) and refinement diff (unified diff of the Overseer's changes) per task attempt. Stored as pre-refinement + diff, not two full copies.

#### Overseer Memory Tables

`decisions`, `constraints`, `assumptions`, `lessons`, `overseer_workflow_status` -- owned by trace, read/written by the Overseer via action tools.

### State Machine Transitions

Legal transitions enforced by the trace module. Invalid transitions are hard errors. Every transition emits an event.

**Task statuses:**
- `created` -> `prechecking`
- `prechecking` -> `active`, `precheck_failed`
- `active` -> `postchecking`
- `postchecking` -> `completed`, `postcheck_failed`
- `postcheck_failed` -> `active` (agent retries)
- `postcheck_failed` -> `escalated`
- Any -> `cancelled`

**Run statuses:**
- `created` -> `running`
- `running` -> `paused`, `completed`, `failed`, `aborted`
- `paused` -> `running`, `aborted`

### Mutual Exclusion

Advisory lock per run. Second scheduler on the same run is a hard error. Heartbeat for stale lock detection.

## Migration Strategy

Schema migrations use pgdesign. `pgdesign migrate` diffs schema versions and generates ALTER statements. The schema version is tracked in `schema/orxt.toml` (`meta.version`).

## Write API

```python
class TraceWriter:
    async def create_run(self, intent, config, autonomy_level) -> UUID: ...
    async def transition_run(self, run_id, new_status, reason): ...
    async def create_task(self, parent_task_id, name, task_type, config) -> UUID: ...
    async def transition_task(self, task_id, new_status, reason): ...
    async def create_task_attempt(self, task_id, attempt) -> UUID: ...
    async def complete_task_attempt(self, attempt_id, output, structured_output, check_result, check_verdict, session_id, tokens, cost, duration): ...
    async def fail_task_attempt(self, attempt_id, error, session_id, tokens, cost, duration): ...
    async def write_event(self, run_id, event_type, data, task_id=None): ...
    async def write_transcript_entry(self, session_id, run_id, turn, role, content, tool_calls=None, tokens=None): ...
    async def write_notepad_entry(self, run_id, task_name, agent_name, entry_type, text): ...
    async def create_inbox_item(self, run_id, decision_type, question, options, assumed_option, work_proceeding, contradiction_impact, tags=None, deadline=None, answer_event=None) -> UUID: ...
    async def answer_inbox_item(self, item_id, answer): ...
    async def skip_inbox_item(self, item_id): ...
    async def reject_inbox_item(self, item_id, reason: str): ...
    async def expire_inbox_item(self, item_id): ...
    async def write_context_diff(self, attempt_id, pre_refinement, refinement_diff): ...
    async def write_decision(self, run_id, decision_type, choice, rationale=None) -> UUID: ...
    async def write_constraint(self, run_id, kind, text, tier, args=None) -> UUID: ...
    async def write_assumption(self, run_id, text, scope, inbox_item_id=None) -> UUID: ...
    async def write_lesson(self, run_id, text, relevance_tags, permanent, source_files=None) -> UUID: ...
    async def update_workflow_status(self, workflow_id, current_step, health): ...
    async def write_coherence_summary(self, run_id, summary: str): ...
```

## Read API

```python
async def list_tasks(pool, run_id) -> list[TaskSummary]: ...
async def read_task_attempt(pool, task_id, attempt) -> TaskAttempt | None: ...
async def read_latest_attempt(pool, task_id) -> TaskAttempt | None: ...
async def read_transcript(pool, session_id) -> list[dict]: ...
async def search_transcript(pool, session_id, query: str) -> list[dict]:
    """Substring search (case-insensitive) against transcript content."""
async def read_run_report(pool, run_id) -> RunReport | None: ...
async def list_runs(pool) -> list[RunSummary]: ...
async def read_inbox(pool, run_id, status=None) -> list[InboxItem]: ...
async def read_notepad(pool, run_id) -> list[NotepadEntry]: ...
async def format_notepad(entries) -> str: ...
```

## Live Event Stream

- **In-process callback**: `TraceWriter` accepts an optional async callable invoked with each event
- **Cross-process NOTIFY**: every event INSERT fires NOTIFY on `orxt_events`. The trigger is created in `_schema.py`.

## Crash Recovery

Three-pass idempotent startup recovery:
1. Reclaim interrupted tasks (transition to `cancelled`)
2. Re-evaluate blocked/waiting work
3. Clean orphaned resources (services, locks, stale advisory locks)

## Files

| File | Contents |
|---|---|
| `_types.py` | `RunReport`, `TaskAttempt`, `RunSummary`, `InboxItem`, `NotepadEntry` and related types. |
| `_schema.py` | CREATE TABLE statements. REVOKE statements. Index definitions. NOTIFY trigger. Migration support via pgdesign. |
| `_writer.py` | `TraceWriter` class. All write methods with transaction management, state machine enforcement, and NOTIFY dispatch. |
| `_reader.py` | Read API functions. |
| `_transitions.py` | State machine definitions. Legal transitions per entity type. |
| `_recovery.py` | Three-pass crash recovery. |
| `_lock.py` | Advisory lock acquisition, heartbeat renewal, stale lock detection. |

## What This Module Does NOT Do

- Does not decide when to write (that is the scheduler and Overseer)
- Does not decide what to verify (that is the consuming project)
- Does not enforce retention policies
- Does not own the run directory on the filesystem (all state is in PG)
