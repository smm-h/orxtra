"""Tests for the capability registry."""

from __future__ import annotations

from orxtra.protocols import (
    ALL_SCOPES,
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
    VALID_INJECT_TOKENS,
    Capability,
)
from orxtra.services._registry import (
    get_capabilities,
    get_capability,
    get_capability_fn,
)

# All expected capability names
EXPECTED_NAMES: set[str] = {
    # Run
    "start_run",
    "list_runs",
    "get_run",
    "abort_run",
    "pause_run",
    "resume_run",
    # Inbox
    "list_inbox",
    "get_inbox_item",
    "respond_to_inbox",
    "skip_inbox_item",
    "reject_inbox_item",
    # Trace
    "list_tasks",
    "get_task_attempts",
    "get_transcript",
    "search_transcript",
    "query_events",
    "get_notepad",
    # Events
    "fire_event",
    # Config
    "show_config",
    "show_pricing",
    # Validate
    "validate_agent",
    "validate_workflow",
    "validate_categories",
    # Dispatch
    "subscribe",
    "unsubscribe",
    "list_subscriptions",
    "create_source",
    "get_source",
    "get_source_by_slug",
    "list_sources",
    "delete_source",
    # Principal
    "create_principal",
    "get_principal",
    "list_principals",
    "delete_principal",
}


def test_get_capabilities_returns_all() -> None:
    caps = get_capabilities()
    names = {c.name for c in caps}
    assert names == EXPECTED_NAMES


def test_get_capabilities_returns_copy() -> None:
    first = get_capabilities()
    second = get_capabilities()
    assert first == second
    assert first is not second


def test_all_capabilities_are_capability_instances() -> None:
    for cap in get_capabilities():
        assert isinstance(cap, Capability)


def test_each_capability_has_params_model() -> None:
    for cap in get_capabilities():
        assert cap.params_model is not None
        # Verify it's a pydantic BaseModel subclass
        assert hasattr(cap.params_model, "model_json_schema")


def test_no_duplicate_names() -> None:
    names = [c.name for c in get_capabilities()]
    assert len(names) == len(set(names))


def test_get_capability_found() -> None:
    cap = get_capability("list_runs")
    assert cap is not None
    assert cap.name == "list_runs"


def test_get_capability_not_found() -> None:
    assert get_capability("nonexistent") is None


def test_get_capability_fn_for_each() -> None:
    for cap in get_capabilities():
        fn = get_capability_fn(cap.name)
        assert callable(fn), f"No function for capability {cap.name!r}"


def test_get_capability_fn_unknown_raises() -> None:
    try:
        get_capability_fn("nonexistent")
    except KeyError:
        pass
    else:
        msg = "Expected KeyError"
        raise AssertionError(msg)


def test_capability_namespaces() -> None:
    """Each capability should have a non-empty namespace."""
    for cap in get_capabilities():
        assert cap.namespace, f"Capability {cap.name!r} has empty namespace"


def test_capability_descriptions() -> None:
    """Each capability should have a non-empty description."""
    for cap in get_capabilities():
        assert cap.description, f"Capability {cap.name!r} has empty description"


def test_capability_tags() -> None:
    """Each capability should have at least one tag."""
    for cap in get_capabilities():
        assert len(cap.tags) > 0, f"Capability {cap.name!r} has no tags"


def test_readonly_and_mutating_tags() -> None:
    """Each capability should be tagged either readonly or mutating."""
    for cap in get_capabilities():
        assert "readonly" in cap.tags or "mutating" in cap.tags, (
            f"Capability {cap.name!r} is neither readonly nor mutating"
        )
        assert not ("readonly" in cap.tags and "mutating" in cap.tags), (
            f"Capability {cap.name!r} is both readonly and mutating"
        )


# Documentation-as-test: the exact scope every capability requires. Changing a
# capability's scope must be a conscious act that updates this pinned mapping.
EXPECTED_SCOPES: dict[str, str] = {
    "start_run": SCOPE_RUNS_MANAGE,
    "list_runs": SCOPE_RUNS_READ,
    "get_run": SCOPE_RUNS_READ,
    "abort_run": SCOPE_RUNS_MANAGE,
    "pause_run": SCOPE_RUNS_MANAGE,
    "resume_run": SCOPE_RUNS_MANAGE,
    "list_inbox": SCOPE_INBOX_READ,
    "get_inbox_item": SCOPE_INBOX_READ,
    "respond_to_inbox": SCOPE_INBOX_RESPOND,
    "skip_inbox_item": SCOPE_INBOX_RESPOND,
    "reject_inbox_item": SCOPE_INBOX_RESPOND,
    "list_tasks": SCOPE_TRACE_READ,
    "get_task_attempts": SCOPE_TRACE_READ,
    "get_transcript": SCOPE_TRACE_READ,
    "search_transcript": SCOPE_TRACE_READ,
    "query_events": SCOPE_TRACE_READ,
    "get_notepad": SCOPE_TRACE_READ,
    "fire_event": SCOPE_EVENTS_WRITE,
    "show_config": SCOPE_CONFIG_READ,
    "show_pricing": SCOPE_CONFIG_READ,
    "validate_agent": SCOPE_VALIDATE_READ,
    "validate_workflow": SCOPE_VALIDATE_READ,
    "validate_categories": SCOPE_VALIDATE_READ,
    "subscribe": SCOPE_SUBSCRIPTIONS_MANAGE,
    "unsubscribe": SCOPE_SUBSCRIPTIONS_MANAGE,
    "list_subscriptions": SCOPE_SUBSCRIPTIONS_READ,
    "create_source": SCOPE_SOURCES_MANAGE,
    "get_source": SCOPE_SOURCES_READ,
    "get_source_by_slug": SCOPE_SOURCES_READ,
    "list_sources": SCOPE_SOURCES_READ,
    "delete_source": SCOPE_SOURCES_MANAGE,
    "create_principal": SCOPE_PRINCIPALS_MANAGE,
    "get_principal": SCOPE_PRINCIPALS_READ,
    "list_principals": SCOPE_PRINCIPALS_READ,
    "delete_principal": SCOPE_PRINCIPALS_MANAGE,
}

# Documentation-as-test: the exact infrastructure each capability receives.
# Must match the dispatcher's routing 1:1 (no behavior change this phase).
EXPECTED_INJECTS: dict[str, frozenset[str]] = {
    "start_run": frozenset({"pool", "principal_storage", "caller_principal"}),
    "list_runs": frozenset({"pool"}),
    "get_run": frozenset({"pool"}),
    "abort_run": frozenset({"pool"}),
    "pause_run": frozenset({"pool"}),
    "resume_run": frozenset({"pool"}),
    "list_inbox": frozenset({"pool"}),
    "get_inbox_item": frozenset({"pool"}),
    "respond_to_inbox": frozenset({"pool", "caller_principal"}),
    "skip_inbox_item": frozenset({"pool", "caller_principal"}),
    "reject_inbox_item": frozenset({"pool", "caller_principal"}),
    "list_tasks": frozenset({"pool"}),
    "get_task_attempts": frozenset({"pool"}),
    "get_transcript": frozenset({"pool"}),
    "search_transcript": frozenset({"pool"}),
    "query_events": frozenset({"pool"}),
    "get_notepad": frozenset({"pool"}),
    "fire_event": frozenset({"pool", "caller_principal"}),
    "show_config": frozenset({"pool"}),
    "show_pricing": frozenset(),
    "validate_agent": frozenset(),
    "validate_workflow": frozenset(),
    "validate_categories": frozenset(),
    "subscribe": frozenset({"dispatch_backend"}),
    "unsubscribe": frozenset({"dispatch_backend"}),
    "list_subscriptions": frozenset({"dispatch_backend"}),
    "create_source": frozenset(
        {"pool", "dispatch_backend", "principal_storage", "caller_principal"},
    ),
    "get_source": frozenset({"dispatch_backend"}),
    "get_source_by_slug": frozenset({"dispatch_backend"}),
    "list_sources": frozenset({"dispatch_backend"}),
    "delete_source": frozenset({"dispatch_backend"}),
    "create_principal": frozenset({"principal_storage", "kind_registry"}),
    "get_principal": frozenset({"principal_storage"}),
    "list_principals": frozenset({"principal_storage"}),
    "delete_principal": frozenset({"principal_storage"}),
}


def test_every_capability_declares_known_scope() -> None:
    """Each capability's required_scope must be a member of the scope vocabulary."""
    for cap in get_capabilities():
        assert cap.required_scope in ALL_SCOPES, (
            f"Capability {cap.name!r} declares unknown scope {cap.required_scope!r}"
        )


def test_every_capability_injects_subset_of_valid_tokens() -> None:
    """Each capability's injects must be a subset of the valid token vocabulary."""
    for cap in get_capabilities():
        assert cap.injects <= VALID_INJECT_TOKENS, (
            f"Capability {cap.name!r} declares invalid inject tokens "
            f"{cap.injects - VALID_INJECT_TOKENS}"
        )


def test_capability_scope_mapping_is_pinned() -> None:
    """The full name->required_scope mapping is pinned exactly."""
    actual = {cap.name: cap.required_scope for cap in get_capabilities()}
    assert actual == EXPECTED_SCOPES


def test_capability_inject_mapping_is_pinned() -> None:
    """The full name->injects mapping is pinned exactly."""
    actual = {cap.name: cap.injects for cap in get_capabilities()}
    assert actual == EXPECTED_INJECTS
