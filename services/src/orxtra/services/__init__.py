from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-services")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.services._actions import ServicesActionExecutor, execute_service_action
from orxtra.services._ask import ask, ask_structured, sync_ask
from orxtra.services._config import show_config, show_pricing
from orxtra.services._dispatch import (
    create_source,
    delete_source,
    get_source,
    get_source_by_slug,
    list_sources,
    list_subscriptions,
    subscribe,
    unsubscribe,
)
from orxtra.services._dispatcher import DispatchContext, dispatch
from orxtra.services._events import event_stream, fire_blocking, fire_event
from orxtra.services._flush import AsyncioFlushScheduler
from orxtra.services._inbox import (
    get_inbox_item,
    list_inbox,
    reject_inbox_item,
    respond_to_inbox,
    skip_inbox_item,
)
from orxtra.services._providers import build_transport_registry
from orxtra.services._registry import (
    get_capabilities,
    get_capability,
    get_capability_fn,
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
from orxtra.services._schema import (
    PG_UUIDV7_STUB,
    AsyncpgAdapter,
    AsyncpgTx,
    SchemaError,
    verify_schema,
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
    "PG_UUIDV7_STUB",
    "AsyncioFlushScheduler",
    "AsyncpgAdapter",
    "AsyncpgTx",
    "DispatchContext",
    "RunConfig",
    "SchemaError",
    "ServicesActionExecutor",
    "__version__",
    "abort_run",
    "ask",
    "ask_structured",
    "build_transport_registry",
    "create_source",
    "delete_source",
    "dispatch",
    "event_stream",
    "execute_service_action",
    "fire_blocking",
    "fire_event",
    "get_capabilities",
    "get_capability",
    "get_capability_fn",
    "get_inbox_item",
    "get_notepad",
    "get_run",
    "get_source",
    "get_source_by_slug",
    "get_task_attempts",
    "get_transcript",
    "list_inbox",
    "list_runs",
    "list_sources",
    "list_subscriptions",
    "list_tasks",
    "pause_run",
    "query_events",
    "reject_inbox_item",
    "respond_to_inbox",
    "resume_run",
    "search_transcript",
    "show_config",
    "show_pricing",
    "skip_inbox_item",
    "start_run",
    "start_run_from_file",
    "subscribe",
    "sync_ask",
    "unsubscribe",
    "validate_agent",
    "validate_categories",
    "validate_workflow",
    "verify_schema",
]
