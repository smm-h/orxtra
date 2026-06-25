from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-services")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.services._ask import ask, ask_structured, sync_ask
from orxtra.services._config import dump_config, show_pricing
from orxtra.services._dispatch import list_subscriptions, subscribe, unsubscribe
from orxtra.services._providers import build_transport_registry
from orxtra.services._events import event_stream, fire_blocking, fire_event
from orxtra.services._inbox import (
    get_inbox_item,
    list_inbox,
    reject_inbox_item,
    respond_to_inbox,
    skip_inbox_item,
)
from orxtra.services._run import (
    RunConfig,
    abort_run,
    get_run,
    list_runs,
    pause_run,
    resume_run,
    start_run,
    start_run_from_file,
)
from orxtra.services._trace import (
    get_notepad,
    get_task_attempts,
    get_transcript,
    list_tasks,
    query_events,
    search_transcript,
)
from orxtra.services._validate import (
    validate_agent,
    validate_categories,
    validate_workflow,
)

__all__ = [
    "__version__",
    "ask",
    "ask_structured",
    "sync_ask",
    "RunConfig",
    "abort_run",
    "build_transport_registry",
    "dump_config",
    "event_stream",
    "fire_blocking",
    "fire_event",
    "get_inbox_item",
    "get_notepad",
    "get_run",
    "get_task_attempts",
    "get_transcript",
    "list_inbox",
    "list_runs",
    "list_subscriptions",
    "list_tasks",
    "pause_run",
    "query_events",
    "reject_inbox_item",
    "respond_to_inbox",
    "resume_run",
    "search_transcript",
    "show_pricing",
    "skip_inbox_item",
    "start_run",
    "start_run_from_file",
    "subscribe",
    "unsubscribe",
    "validate_agent",
    "validate_categories",
    "validate_workflow",
]
