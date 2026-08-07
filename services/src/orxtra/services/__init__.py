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
from orxtra.services._dispatch_worker import create_dispatch_worker
from orxtra.services._dispatcher import DispatchContext, dispatch
from orxtra.services._events import event_stream, fire_blocking, fire_event
from orxtra.services._flush import AsyncioFlushScheduler
from orxtra.services._identity import (
    create_principal,
    delete_principal,
    get_principal,
    list_principals,
    sweep_orphaned_run_principals,
)
from orxtra.services._inbox import (
    get_inbox_item,
    list_inbox,
    reject_inbox_item,
    respond_to_inbox,
    skip_inbox_item,
)
from orxtra.services._notifications import (
    acknowledge_delivery,
    list_deliveries,
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
from orxtra.services._run_manager import RunManager
from orxtra.services._schema import (
    AsyncpgAdapter,
    AsyncpgTx,
    SchemaError,
    verify_schema,
    verify_schema_objects,
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
    "AsyncioFlushScheduler",
    "AsyncpgAdapter",
    "AsyncpgTx",
    "DispatchContext",
    "RunConfig",
    "RunManager",
    "SchemaError",
    "ServicesActionExecutor",
    "__version__",
    "abort_run",
    "acknowledge_delivery",
    "ask",
    "ask_structured",
    "build_transport_registry",
    "create_dispatch_worker",
    "create_principal",
    "create_source",
    "delete_principal",
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
    "get_principal",
    "get_run",
    "get_source",
    "get_source_by_slug",
    "get_task_attempts",
    "get_transcript",
    "list_deliveries",
    "list_inbox",
    "list_principals",
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
    "sweep_orphaned_run_principals",
    "sync_ask",
    "unsubscribe",
    "validate_agent",
    "validate_categories",
    "validate_workflow",
    "verify_schema",
    "verify_schema_objects",
]
