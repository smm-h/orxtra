"""Capability registry: maps every capability-eligible service function
to a Capability descriptor and its implementation function.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from orxtra.protocols import (
    SCOPE_CONFIG_READ,
    SCOPE_EVENTS_WRITE,
    SCOPE_INBOX_READ,
    SCOPE_INBOX_RESPOND,
    SCOPE_PRINCIPALS_MANAGE,
    SCOPE_PRINCIPALS_READ,
    SCOPE_RUNS_MANAGE,
    SCOPE_RUNS_READ,
    SCOPE_SOURCES_MANAGE,
    SCOPE_SOURCES_READ,
    SCOPE_SUBSCRIPTIONS_MANAGE,
    SCOPE_SUBSCRIPTIONS_READ,
    SCOPE_TRACE_READ,
    SCOPE_VALIDATE_READ,
    Capability,
)
from orxtra.services._params import (
    AbortRunParams,
    CreatePrincipalParams,
    CreateSourceParams,
    DeletePrincipalParams,
    DeleteSourceParams,
    FireEventParams,
    GetInboxItemParams,
    GetNotepadParams,
    GetPrincipalParams,
    GetRunParams,
    GetSourceBySlugParams,
    GetSourceParams,
    GetTaskAttemptsParams,
    GetTranscriptParams,
    ListInboxParams,
    ListPrincipalsParams,
    ListRunsParams,
    ListSourcesParams,
    ListSubscriptionsParams,
    ListTasksParams,
    PauseRunParams,
    QueryEventsParams,
    RejectInboxItemParams,
    RespondToInboxParams,
    ResumeRunParams,
    SearchTranscriptParams,
    ShowConfigParams,
    ShowPricingParams,
    SkipInboxItemParams,
    StartRunParams,
    SubscribeParams,
    UnsubscribeParams,
    ValidateAgentParams,
    ValidateCategoriesParams,
    ValidateWorkflowParams,
)

# Lazy-import service functions to avoid circular imports at module scope.
# The functions are resolved on first access via get_capability_fn().

ServiceFn = Callable[..., Coroutine[Any, Any, Any]]

# Infrastructure injection token sets, matching the dispatcher's routing:
#  - pool-backed capabilities receive the asyncpg pool
#  - dispatch-backed capabilities receive the DispatchBackend
#  - pure capabilities receive neither (validated params only)
_INJECT_POOL: frozenset[str] = frozenset({"pool"})
_INJECT_DISPATCH_BACKEND: frozenset[str] = frozenset({"dispatch_backend"})
_INJECT_PRINCIPAL_STORAGE: frozenset[str] = frozenset({"principal_storage"})
_INJECT_PRINCIPAL_STORAGE_AND_KIND_REGISTRY: frozenset[str] = frozenset({
    "principal_storage",
    "kind_registry",
})
_INJECT_NONE: frozenset[str] = frozenset()


def _build_capabilities() -> list[Capability]:
    return [
        # -- Run --
        Capability(
            name="start_run",
            namespace="run",
            description="Start a run from a config file",
            params_model=StartRunParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="run",
            required_scope=SCOPE_RUNS_MANAGE,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="list_runs",
            namespace="run",
            description="List all runs",
            params_model=ListRunsParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="run",
            required_scope=SCOPE_RUNS_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="get_run",
            namespace="run",
            description="Get a run's full report",
            params_model=GetRunParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="run",
            required_scope=SCOPE_RUNS_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="abort_run",
            namespace="run",
            description="Abort a running run",
            params_model=AbortRunParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="run",
            required_scope=SCOPE_RUNS_MANAGE,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="pause_run",
            namespace="run",
            description="Pause a running run",
            params_model=PauseRunParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="run",
            required_scope=SCOPE_RUNS_MANAGE,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="resume_run",
            namespace="run",
            description="Resume a paused run",
            params_model=ResumeRunParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="run",
            required_scope=SCOPE_RUNS_MANAGE,
            injects=_INJECT_POOL,
        ),
        # -- Inbox --
        Capability(
            name="list_inbox",
            namespace="inbox",
            description="List inbox items for a run",
            params_model=ListInboxParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="inbox",
            required_scope=SCOPE_INBOX_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="get_inbox_item",
            namespace="inbox",
            description="Get a single inbox item",
            params_model=GetInboxItemParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="inbox",
            required_scope=SCOPE_INBOX_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="respond_to_inbox",
            namespace="inbox",
            description="Answer an inbox item",
            params_model=RespondToInboxParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="inbox",
            required_scope=SCOPE_INBOX_RESPOND,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="skip_inbox_item",
            namespace="inbox",
            description="Skip an inbox item",
            params_model=SkipInboxItemParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="inbox",
            required_scope=SCOPE_INBOX_RESPOND,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="reject_inbox_item",
            namespace="inbox",
            description="Reject an inbox item",
            params_model=RejectInboxItemParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="inbox",
            required_scope=SCOPE_INBOX_RESPOND,
            injects=_INJECT_POOL,
        ),
        # -- Trace --
        Capability(
            name="list_tasks",
            namespace="trace",
            description="List tasks for a run",
            params_model=ListTasksParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="trace",
            required_scope=SCOPE_TRACE_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="get_task_attempts",
            namespace="trace",
            description="Get attempts for a task",
            params_model=GetTaskAttemptsParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="trace",
            required_scope=SCOPE_TRACE_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="get_transcript",
            namespace="trace",
            description="Get a session transcript",
            params_model=GetTranscriptParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="trace",
            required_scope=SCOPE_TRACE_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="search_transcript",
            namespace="trace",
            description="Search a transcript",
            params_model=SearchTranscriptParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="trace",
            required_scope=SCOPE_TRACE_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="query_events",
            namespace="trace",
            description="Query events for a run",
            params_model=QueryEventsParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="trace",
            required_scope=SCOPE_TRACE_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="get_notepad",
            namespace="trace",
            description="Get notepad entries for a run",
            params_model=GetNotepadParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="trace",
            required_scope=SCOPE_TRACE_READ,
            injects=_INJECT_POOL,
        ),
        # -- Events --
        Capability(
            name="fire_event",
            namespace="event",
            description="Fire a named event for wait-for tasks",
            params_model=FireEventParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="event",
            required_scope=SCOPE_EVENTS_WRITE,
            injects=_INJECT_POOL,
        ),
        # -- Config --
        Capability(
            name="show_config",
            namespace="config",
            description="Show a run's config snapshot",
            params_model=ShowConfigParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="config",
            required_scope=SCOPE_CONFIG_READ,
            injects=_INJECT_POOL,
        ),
        Capability(
            name="show_pricing",
            namespace="config",
            description="Show the pricing table",
            params_model=ShowPricingParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="config",
            required_scope=SCOPE_CONFIG_READ,
            injects=_INJECT_NONE,
        ),
        # -- Validate --
        Capability(
            name="validate_agent",
            namespace="validate",
            description="Validate an agent TOML file",
            params_model=ValidateAgentParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="validate",
            required_scope=SCOPE_VALIDATE_READ,
            injects=_INJECT_NONE,
        ),
        Capability(
            name="validate_workflow",
            namespace="validate",
            description="Validate a workflow TOML file",
            params_model=ValidateWorkflowParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="validate",
            required_scope=SCOPE_VALIDATE_READ,
            injects=_INJECT_NONE,
        ),
        Capability(
            name="validate_categories",
            namespace="validate",
            description="Validate a categories TOML file",
            params_model=ValidateCategoriesParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="validate",
            required_scope=SCOPE_VALIDATE_READ,
            injects=_INJECT_NONE,
        ),
        # -- Dispatch --
        Capability(
            name="subscribe",
            namespace="dispatch",
            description="Create a subscription with filter and actions",
            params_model=SubscribeParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="dispatch",
            required_scope=SCOPE_SUBSCRIPTIONS_MANAGE,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="unsubscribe",
            namespace="dispatch",
            description="Delete a subscription",
            params_model=UnsubscribeParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="dispatch",
            required_scope=SCOPE_SUBSCRIPTIONS_MANAGE,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="list_subscriptions",
            namespace="dispatch",
            description="List subscriptions",
            params_model=ListSubscriptionsParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="dispatch",
            required_scope=SCOPE_SUBSCRIPTIONS_READ,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="create_source",
            namespace="dispatch",
            description="Create a new event source",
            params_model=CreateSourceParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="dispatch",
            required_scope=SCOPE_SOURCES_MANAGE,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="get_source",
            namespace="dispatch",
            description="Get a source by ID",
            params_model=GetSourceParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="dispatch",
            required_scope=SCOPE_SOURCES_READ,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="get_source_by_slug",
            namespace="dispatch",
            description="Get a source by slug",
            params_model=GetSourceBySlugParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="dispatch",
            required_scope=SCOPE_SOURCES_READ,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="list_sources",
            namespace="dispatch",
            description="List all registered sources",
            params_model=ListSourcesParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="dispatch",
            required_scope=SCOPE_SOURCES_READ,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        Capability(
            name="delete_source",
            namespace="dispatch",
            description="Delete a source by ID",
            params_model=DeleteSourceParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="dispatch",
            required_scope=SCOPE_SOURCES_MANAGE,
            injects=_INJECT_DISPATCH_BACKEND,
        ),
        # -- Principal --
        Capability(
            name="create_principal",
            namespace="principal",
            description="Create (idempotently mint) a principal",
            params_model=CreatePrincipalParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="principal",
            required_scope=SCOPE_PRINCIPALS_MANAGE,
            injects=_INJECT_PRINCIPAL_STORAGE_AND_KIND_REGISTRY,
        ),
        Capability(
            name="get_principal",
            namespace="principal",
            description="Get a principal by ID",
            params_model=GetPrincipalParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="principal",
            required_scope=SCOPE_PRINCIPALS_READ,
            injects=_INJECT_PRINCIPAL_STORAGE,
        ),
        Capability(
            name="list_principals",
            namespace="principal",
            description="List principals, optionally filtered by kind",
            params_model=ListPrincipalsParams,
            result_model=None,
            tags=frozenset({"readonly"}),
            category="principal",
            required_scope=SCOPE_PRINCIPALS_READ,
            injects=_INJECT_PRINCIPAL_STORAGE,
        ),
        Capability(
            name="delete_principal",
            namespace="principal",
            description="Delete a principal (refuses the system principal)",
            params_model=DeletePrincipalParams,
            result_model=None,
            tags=frozenset({"mutating"}),
            category="principal",
            required_scope=SCOPE_PRINCIPALS_MANAGE,
            injects=_INJECT_PRINCIPAL_STORAGE,
        ),
    ]


CAPABILITIES: list[Capability] = _build_capabilities()

_CAPABILITIES_BY_NAME: dict[str, Capability] = {c.name: c for c in CAPABILITIES}


def get_capabilities() -> list[Capability]:
    """Return the list of all registered capabilities."""
    return list(CAPABILITIES)


def get_capability(name: str) -> Capability | None:
    """Look up a capability by name, or None if not found."""
    return _CAPABILITIES_BY_NAME.get(name)


def _get_fn_map() -> dict[str, ServiceFn]:
    """Build the mapping from capability name to service function.

    Uses lazy imports to avoid circular dependencies at module load time.
    """
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
    from orxtra.services._events import fire_event
    from orxtra.services._identity import (
        create_principal,
        delete_principal,
        get_principal,
        list_principals,
    )
    from orxtra.services._inbox import (
        get_inbox_item,
        list_inbox,
        reject_inbox_item,
        respond_to_inbox,
        skip_inbox_item,
    )
    from orxtra.services._run import (
        abort_run,
        get_run,
        list_runs,
        pause_run,
        resume_run,
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

    return {
        "start_run": start_run_from_file,
        "list_runs": list_runs,
        "get_run": get_run,
        "abort_run": abort_run,
        "pause_run": pause_run,
        "resume_run": resume_run,
        "list_inbox": list_inbox,
        "get_inbox_item": get_inbox_item,
        "respond_to_inbox": respond_to_inbox,
        "skip_inbox_item": skip_inbox_item,
        "reject_inbox_item": reject_inbox_item,
        "list_tasks": list_tasks,
        "get_task_attempts": get_task_attempts,
        "get_transcript": get_transcript,
        "search_transcript": search_transcript,
        "query_events": query_events,
        "get_notepad": get_notepad,
        "fire_event": fire_event,
        "show_config": show_config,
        "show_pricing": show_pricing,
        "validate_agent": validate_agent,
        "validate_workflow": validate_workflow,
        "validate_categories": validate_categories,
        "subscribe": subscribe,
        "unsubscribe": unsubscribe,
        "list_subscriptions": list_subscriptions,
        "create_source": create_source,
        "get_source": get_source,
        "get_source_by_slug": get_source_by_slug,
        "list_sources": list_sources,
        "delete_source": delete_source,
        "create_principal": create_principal,
        "get_principal": get_principal,
        "list_principals": list_principals,
        "delete_principal": delete_principal,
    }


_FN_MAP: dict[str, ServiceFn] | None = None


def get_capability_fn(name: str) -> ServiceFn:
    """Get the service function for a capability by name.

    Raises KeyError if the capability name is not registered.
    """
    global _FN_MAP  # noqa: PLW0603
    if _FN_MAP is None:
        _FN_MAP = _get_fn_map()
    return _FN_MAP[name]
