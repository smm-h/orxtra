# Services Module Design

Shared business logic consumed by all three frontends: the Python API, the strictcli CLI, and the MCP server.

## Responsibility

Implement the operations that the frontends expose. Each service is a set of async functions that take an asyncpg pool (and operation-specific parameters) and return typed results. The frontends are thin projections -- they parse input, call a service function, and format output. Logic lives here.

## Services

### Run Service

Operations on runs.

```python
async def start_run(pool, intent: str, config: RunConfig) -> uuid.UUID:
    """Create a run, persist config snapshot, acquire advisory lock, start the scheduler."""

async def get_run(pool, run_id: uuid.UUID) -> RunReport:
    """Read a run's full report."""

async def list_runs(pool) -> list[RunSummary]:
    """List all runs, newest first."""

async def abort_run(pool, run_id: uuid.UUID) -> None:
    """Signal the scheduler to abort a run."""

async def pause_run(pool, run_id: uuid.UUID) -> None:
    """Signal the scheduler to pause a run."""

async def resume_run(pool, run_id: uuid.UUID) -> None:
    """Resume a paused run."""
```

### Inbox Service

Operations on human inbox items.

```python
async def list_inbox(pool, run_id: uuid.UUID, status: str | None = None) -> list[InboxItem]:
    """List inbox items for a run, optionally filtered by status."""

async def get_inbox_item(pool, item_id: uuid.UUID) -> InboxItem:
    """Read a single inbox item."""

async def respond_to_inbox(pool, item_id: uuid.UUID, answer: str) -> InboxItem:
    """Answer an inbox item. Fires the answer_event if declared."""

async def skip_inbox_item(pool, item_id: uuid.UUID) -> InboxItem:
    """Skip an inbox item (assumption permanently blessed)."""
```

### Trace Service

Read-only queries against the trace schema.

```python
async def get_step_attempts(pool, step_id: uuid.UUID) -> list[StepAttempt]:
    """All attempts for a step."""

async def get_transcript(pool, session_id: str) -> list[dict]:
    """Full conversation transcript for a session."""

async def query_events(pool, run_id: uuid.UUID, event_type: str | None = None, since: datetime | None = None, limit: int = 100) -> list[dict]:
    """Query events for a run."""

async def get_notepad(pool, run_id: uuid.UUID) -> list[NotepadEntry]:
    """Read notepad entries for a run."""
```

### Validation Service

Offline validation of agent/workflow/category TOML files.

```python
async def validate_agent(path: Path) -> list[str]:
    """Validate an agent TOML file. Returns list of error messages (empty = valid)."""

async def validate_workflow(path: Path) -> list[str]:
    """Validate a workflow TOML file against the step schema."""

async def validate_categories(path: Path) -> list[str]:
    """Validate a categories TOML file."""
```

### Config Service

Configuration inspection.

```python
async def dump_config(pool, run_id: uuid.UUID) -> dict:
    """Read the config snapshot for a run."""

async def show_pricing() -> dict:
    """Show the current internal pricing table."""
```

## Design Decisions

**Async throughout.** Every service function is async (asyncpg is async). The CLI wraps calls in `asyncio.run()`.

**Pool, not connection.** Services take a pool, acquire connections internally, and release them. No connection leaks across call boundaries.

**Typed returns.** Services return pydantic models or typed dataclasses, not raw dicts. The frontends format these for their medium (JSON for MCP, tables for CLI, Python objects for the API).

**No state.** Services are stateless functions, not classes with lifecycle. The pool is the only shared resource.

## Files

| File | Contents |
|---|---|
| `_run.py` | Run service: start, get, list, abort, pause, resume. |
| `_inbox.py` | Inbox service: list, get, respond, skip. |
| `_trace.py` | Trace query service: step attempts, transcripts, events, notepad. |
| `_validate.py` | Validation service: agents, workflows, categories. |
| `_config.py` | Config service: dump, pricing. |

## What This Module Does NOT Do

- Does not format output for any specific frontend (that's cli/, mcp/, or the consumer's code)
- Does not manage the database schema (that's trace/)
- Does not implement the scheduler's event loop
- Does not make design decisions or judgment calls
