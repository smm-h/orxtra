"""Tests for the capability registry."""

from __future__ import annotations

from orxtra.protocols import Capability
from orxtra.services._registry import (
    CAPABILITIES,
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
