from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.a2ui._engine import TemplateEngine
from orxtra.a2ui._fragments import FragmentLibrary
from orxtra.a2ui._registry import SurfaceRegistry

if TYPE_CHECKING:
    from orxtra.protocols import SurfaceOperation

_engine = TemplateEngine()
_fragments = FragmentLibrary()


def inbox_approval_template(data: dict[str, Any]) -> list[SurfaceOperation]:
    """Surface for inbox approval items.

    Expected data keys: /question, /options, /assumed_option,
    /contradiction_impact, /tags
    """
    components: list[dict[str, Any]] = [
        {
            "id": "inbox-card",
            "component": "Card",
            "properties": {
                "children": [
                    {
                        "id": "inbox-question",
                        "component": "Text",
                        "properties": {"content": "$/question"},
                    },
                    {
                        "id": "inbox-options",
                        "component": "List",
                        "properties": {
                            "items": "$/options",
                            "item_template": {
                                "id": "option-item",
                                "component": "Text",
                                "properties": {"label": "$/label"},
                            },
                        },
                    },
                    {
                        "id": "inbox-assumed",
                        "component": "Text",
                        "properties": {
                            "content": "$/assumed_option",
                            "highlight": True,
                        },
                    },
                    {
                        "id": "inbox-impact",
                        "component": "Text",
                        "properties": {"content": "$/contradiction_impact"},
                    },
                    *_fragments.approval_button("answer"),
                    *_fragments.approval_button("skip"),
                    *_fragments.approval_button("reject"),
                ],
            },
        },
    ]
    return _engine.populate(
        components, data, surface_id="inbox-approval", catalog_id="inbox",
    )


def task_summary_template(data: dict[str, Any]) -> list[SurfaceOperation]:
    """Surface for task summary display.

    Expected data keys: /name, /state, /type, /agent, /attempt_count, /cost_usd
    """
    components: list[dict[str, Any]] = [
        {
            "id": "task-summary-card",
            "component": "Card",
            "properties": {
                "children": [
                    {
                        "id": "task-name",
                        "component": "Text",
                        "properties": {"content": "$/name"},
                    },
                    {
                        "id": "task-state",
                        "component": "Text",
                        "properties": {"content": "$/state"},
                    },
                    {
                        "id": "task-type",
                        "component": "Text",
                        "properties": {"content": "$/type"},
                    },
                    {
                        "id": "task-agent",
                        "component": "Text",
                        "properties": {"content": "$/agent"},
                    },
                    {
                        "id": "task-attempts",
                        "component": "Text",
                        "properties": {"content": "$/attempt_count"},
                    },
                    {
                        "id": "task-cost",
                        "component": "Text",
                        "properties": {"content": "$/cost_usd"},
                    },
                ],
            },
        },
    ]
    return _engine.populate(
        components, data, surface_id="task-summary", catalog_id="task",
    )


def budget_gauge_template(data: dict[str, Any]) -> list[SurfaceOperation]:
    """Surface for budget gauge display.

    Expected data keys: /spent_usd, /budget_usd
    """
    spent = data.get("spent_usd", 0)
    budget = data.get("budget_usd", 1)
    percentage = (spent / budget * 100) if budget > 0 else 0
    enriched = {**data, "formatted_percentage": f"{percentage:.1f}%"}

    components: list[dict[str, Any]] = [
        {
            "id": "budget-gauge",
            "component": "Text",
            "properties": {"content": "$/formatted_percentage"},
        },
    ]
    return _engine.populate(
        components, enriched, surface_id="budget-gauge", catalog_id="budget",
    )


def check_verdict_template(data: dict[str, Any]) -> list[SurfaceOperation]:
    """Surface for check verdict display.

    Expected data keys: /issues (array of {severity, file, line, description})
    """
    components: list[dict[str, Any]] = [
        {
            "id": "check-verdict-list",
            "component": "List",
            "properties": {
                "items": "$/issues",
                "item_template": {
                    "id": "issue-item",
                    "component": "Text",
                    "properties": {
                        "severity": "$/severity",
                        "file": "$/file",
                        "line": "$/line",
                        "description": "$/description",
                    },
                },
            },
        },
    ]
    return _engine.populate(
        components, data, surface_id="check-verdict", catalog_id="check",
    )


def event_entry_template(data: dict[str, Any]) -> list[SurfaceOperation]:
    """Surface for event entry display.

    Expected data keys: /event_type, /timestamp, /summary
    """
    components: list[dict[str, Any]] = [
        {
            "id": "event-entry",
            "component": "Text",
            "properties": {
                "event_type": "$/event_type",
                "timestamp": "$/timestamp",
                "summary": "$/summary",
            },
        },
    ]
    return _engine.populate(
        components, data, surface_id="event-entry", catalog_id="event",
    )


def build_default_registry() -> SurfaceRegistry:
    """Build a SurfaceRegistry pre-loaded with all standard templates."""
    registry = SurfaceRegistry()
    registry.register("inbox_approval", inbox_approval_template)
    registry.register("task_summary", task_summary_template)
    registry.register("budget_gauge", budget_gauge_template)
    registry.register("check_verdict", check_verdict_template)
    registry.register("event_entry", event_entry_template)
    return registry


default_registry: SurfaceRegistry = build_default_registry()
