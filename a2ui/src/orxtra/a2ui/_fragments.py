from __future__ import annotations

from typing import Any


class FragmentLibrary:
    """Pre-built composable UI fragments.

    Each method returns a list of A2UI-compliant component dicts
    with ``id``, ``component`` type name, and data-bound properties
    using JSON Pointer references (``$`` prefix).
    """

    @staticmethod
    def task_card(task_id: str) -> list[dict[str, Any]]:
        """Card with Text fields for task name/state/type/agent/attempts/cost."""
        return [
            {
                "id": f"task-card-{task_id}",
                "component": "Card",
                "properties": {
                    "children": [
                        {
                            "id": f"task-name-{task_id}",
                            "component": "Text",
                            "properties": {"content": "$/name"},
                        },
                        {
                            "id": f"task-state-{task_id}",
                            "component": "Text",
                            "properties": {"content": "$/state"},
                        },
                        {
                            "id": f"task-type-{task_id}",
                            "component": "Text",
                            "properties": {"content": "$/type"},
                        },
                        {
                            "id": f"task-agent-{task_id}",
                            "component": "Text",
                            "properties": {"content": "$/agent"},
                        },
                        {
                            "id": f"task-attempts-{task_id}",
                            "component": "Text",
                            "properties": {"content": "$/attempt_count"},
                        },
                        {
                            "id": f"task-cost-{task_id}",
                            "component": "Text",
                            "properties": {"content": "$/cost_usd"},
                        },
                    ],
                },
            },
        ]

    @staticmethod
    def budget_meter(path_prefix: str) -> list[dict[str, Any]]:
        """Text showing formatted budget percentage."""
        return [
            {
                "id": f"budget-meter-{path_prefix.strip('/')}",
                "component": "Text",
                "properties": {
                    "content": f"${path_prefix.rstrip('/')}/formatted_percentage",
                },
            },
        ]

    @staticmethod
    def approval_button(action_name: str) -> list[dict[str, Any]]:
        """Button with a server action."""
        return [
            {
                "id": f"approval-btn-{action_name}",
                "component": "Button",
                "properties": {
                    "label": action_name,
                    "action": {"type": "server", "name": action_name},
                },
            },
        ]

    @staticmethod
    def check_result_list(path_prefix: str) -> list[dict[str, Any]]:
        """List of check issues bound to a path."""
        return [
            {
                "id": f"check-results-{path_prefix.strip('/')}",
                "component": "List",
                "properties": {
                    "items": f"${path_prefix.rstrip('/')}/issues",
                    "item_template": {
                        "id": "check-issue-item",
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

    @staticmethod
    def text_block(text_path: str) -> list[dict[str, Any]]:
        """Simple Text bound to a path."""
        path_id = text_path.strip("/").replace("/", "-")
        return [
            {
                "id": f"text-{path_id}",
                "component": "Text",
                "properties": {"content": f"${text_path}"},
            },
        ]

    @staticmethod
    def event_entry(path_prefix: str) -> list[dict[str, Any]]:
        """Text with type/timestamp/summary for an event."""
        prefix = path_prefix.rstrip("/")
        path_id = prefix.strip("/").replace("/", "-")
        return [
            {
                "id": f"event-{path_id}",
                "component": "Text",
                "properties": {
                    "event_type": f"${prefix}/event_type",
                    "timestamp": f"${prefix}/timestamp",
                    "summary": f"${prefix}/summary",
                },
            },
        ]
