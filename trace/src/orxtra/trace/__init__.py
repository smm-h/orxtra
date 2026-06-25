from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-trace")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.trace._lock import (
    RunLockError,
    acquire_run_lock,
    is_lock_stale,
    lock_key,
    release_run_lock,
    update_heartbeat,
)
from orxtra.trace._memory_backend import InMemoryBackend, InMemoryEventBus
from orxtra.trace._pg_backend import PgBackend
from orxtra.trace._pg_event_bus import PgEventBus
from orxtra.trace._protocols import (
    EventBus,
    EventStorage,
    InboxStorage,
    KnowledgeHashStorage,
    NotepadStorage,
    OverseerStorage,
    RecoveryOperations,
    RunControlStorage,
    RunStorage,
    StorageBackend,
    StorageLock,
    StorageReader,
    TaskStorage,
)
from orxtra.trace._reader import (
    list_iterations,
    list_runs,
    list_tasks,
    query_events,
    query_lessons,
    query_relevant_lessons,
    read_active_constraints,
    read_assumptions,
    read_constraints,
    read_decisions,
    read_inbox,
    read_inbox_item,
    read_latest_attempt,
    read_notepad,
    read_run_config,
    read_run_report,
    read_session_token_counts,
    read_session_turn_count,
    read_task_attempt,
    read_task_attempts,
    read_transcript,
    read_workflow_status,
    replay,
    search_transcript,
)
from orxtra.trace._schema import ALL_CREATE_STATEMENTS, TABLE_NAMES
from orxtra.trace._recovery import (
    clean_orphaned,
    reclaim_interrupted,
    reevaluate_blocked,
)
from orxtra.trace._transitions import (
    InvalidTransitionError,
    validate_run_transition,
    validate_task_transition,
)
from orxtra.trace._types import (
    InboxItem,
    IterationResult,
    NotepadEntry,
    RunReport,
    RunSummary,
    TaskAttempt,
    TaskSummary,
)
from orxtra.trace._writer import TraceWriter

__all__ = [
    "ALL_CREATE_STATEMENTS",
    "TABLE_NAMES",
    "__version__",
    "EventBus",
    "EventStorage",
    "InMemoryBackend",
    "InMemoryEventBus",
    "InboxItem",
    "InboxStorage",
    "InvalidTransitionError",
    "KnowledgeHashStorage",
    "IterationResult",
    "NotepadEntry",
    "NotepadStorage",
    "OverseerStorage",
    "PgBackend",
    "PgEventBus",
    "RecoveryOperations",
    "RunControlStorage",
    "RunLockError",
    "RunReport",
    "RunStorage",
    "RunSummary",
    "StorageBackend",
    "StorageLock",
    "StorageReader",
    "TaskAttempt",
    "TaskStorage",
    "TaskSummary",
    "TraceWriter",
    "acquire_run_lock",
    "clean_orphaned",
    "is_lock_stale",
    "list_iterations",
    "list_runs",
    "lock_key",
    "list_tasks",
    "query_events",
    "query_lessons",
    "query_relevant_lessons",
    "read_active_constraints",
    "read_assumptions",
    "read_constraints",
    "read_decisions",
    "read_inbox",
    "read_inbox_item",
    "read_latest_attempt",
    "read_notepad",
    "read_run_config",
    "read_run_report",
    "read_session_token_counts",
    "read_session_turn_count",
    "read_task_attempt",
    "read_task_attempts",
    "read_transcript",
    "read_workflow_status",
    "reclaim_interrupted",
    "replay",
    "reevaluate_blocked",
    "release_run_lock",
    "search_transcript",
    "update_heartbeat",
    "validate_run_transition",
    "validate_task_transition",
]
