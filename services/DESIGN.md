# Services Module Design

Shared business logic consumed by all three frontends: the Python API, the strictcli CLI, and the MCP server.

## Responsibility

Implement the operations that the frontends expose. Each service is a set of async functions that take an asyncpg pool (and operation-specific parameters) and return typed results. Logic lives here. The frontends are thin projections.

## Services

### Run Service

```python
async def start_run(pool, intent: str, config: RunConfig) -> uuid.UUID:
    """Create a run from a RunConfig object (Python API)."""

async def start_run_from_file(pool, intent: str, config_path: Path) -> uuid.UUID:
    """Create a run from a config file (CLI/MCP). Parses and validates the file."""

async def get_run(pool, run_id: uuid.UUID) -> RunReport: ...
async def list_runs(pool) -> list[RunSummary]: ...
async def abort_run(pool, run_id: uuid.UUID) -> None: ...
async def pause_run(pool, run_id: uuid.UUID) -> None: ...
async def resume_run(pool, run_id: uuid.UUID) -> None: ...
```

The config file (TOML) declares: agents directory, knowledge directory, categories path, db_url, provider credentials, budget, autonomy level. The services layer loads, validates, constructs the transport registry, and starts the scheduler.

### Inbox Service

```python
async def list_inbox(pool, run_id, status=None) -> list[InboxItem]: ...
async def get_inbox_item(pool, item_id) -> InboxItem: ...
async def respond_to_inbox(pool, item_id, answer) -> InboxItem: ...
async def skip_inbox_item(pool, item_id) -> InboxItem: ...
async def reject_inbox_item(pool, item_id, reason: str) -> InboxItem:
    """Reject an inbox item. The Overseer must re-investigate and create a new item."""
```

### Trace Service

```python
async def list_tasks(pool, run_id) -> list[TaskSummary]:
    """List all tasks for a run with statuses and attempt counts."""
async def get_task_attempts(pool, task_id) -> list[TaskAttempt]: ...
async def get_transcript(pool, session_id) -> list[dict]: ...
async def search_transcript(pool, session_id, query: str) -> list[dict]:
    """Substring search (case-insensitive) against transcript content."""
async def query_events(pool, run_id, event_type=None, since=None, limit=100) -> list[dict]: ...
async def get_notepad(pool, run_id) -> list[NotepadEntry]: ...
```

### Event Service

```python
async def fire_event(pool, run_id: uuid.UUID, event_name: str, payload: dict | None = None) -> None:
    """Fire a named event for wait-for tasks. Issues PG NOTIFY."""
```

### Validation Service

```python
async def validate_agent(path: Path) -> list[str]: ...
async def validate_workflow(path: Path) -> list[str]: ...
async def validate_categories(path: Path) -> list[str]: ...
```

### Config Service

```python
async def dump_config(pool, run_id) -> dict: ...
async def show_pricing() -> dict: ...
```

## Design Decisions

- **Async throughout.** asyncpg is async. The CLI wraps calls in `asyncio.run()`.
- **Pool, not connection.** Services take a pool, acquire connections internally.
- **Typed returns.** Pydantic models or typed dataclasses, not raw dicts.
- **No state.** Services are stateless functions. The pool is the only shared resource.

## Files

| File | Contents |
|---|---|
| `_run.py` | Run service: start, start_from_file, get, list, abort, pause, resume. |
| `_inbox.py` | Inbox service: list, get, respond, skip, reject. |
| `_trace.py` | Trace query service: task attempts, transcripts, transcript search, events, notepad. |
| `_events.py` | Event service: fire_event. |
| `_validate.py` | Validation service: agents, workflows, categories. |
| `_config.py` | Config service: dump, pricing. |

## What This Module Does NOT Do

- Does not format output for any specific frontend
- Does not manage the database schema (that is trace/)
- Does not implement the scheduler's event loop
