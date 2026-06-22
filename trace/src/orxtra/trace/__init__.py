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
    release_run_lock,
    update_heartbeat,
)
from orxtra.trace._reader import (
    list_iterations,
    list_runs,
    list_tasks,
    read_active_constraints,
    read_inbox,
    read_latest_attempt,
    read_notepad,
    read_run_report,
    read_task_attempt,
    read_transcript,
    search_transcript,
)
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
    "__version__",
    "InboxItem",
    "InvalidTransitionError",
    "IterationResult",
    "NotepadEntry",
    "RunLockError",
    "RunReport",
    "RunSummary",
    "TaskAttempt",
    "TaskSummary",
    "TraceWriter",
    "acquire_run_lock",
    "clean_orphaned",
    "is_lock_stale",
    "list_iterations",
    "list_runs",
    "list_tasks",
    "read_active_constraints",
    "read_inbox",
    "read_latest_attempt",
    "read_notepad",
    "read_run_report",
    "read_task_attempt",
    "read_transcript",
    "reclaim_interrupted",
    "reevaluate_blocked",
    "release_run_lock",
    "search_transcript",
    "update_heartbeat",
    "validate_run_transition",
    "validate_task_transition",
]
