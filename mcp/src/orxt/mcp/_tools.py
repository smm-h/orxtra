from __future__ import annotations

TOOL_DEFINITIONS: list[dict[str, object]] = [
    # -- Run tools --
    {
        "name": "start_run",
        "description": "Start a run from a config file",
        "inputSchema": {
            "type": "object",
            "properties": {
                "config_path": {"type": "string"},
                "intent": {"type": "string"},
            },
            "required": ["config_path", "intent"],
        },
    },
    {
        "name": "list_runs",
        "description": "List all runs",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
    {
        "name": "get_run",
        "description": "Get a run's full report",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "abort_run",
        "description": "Abort a running run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "pause_run",
        "description": "Pause a running run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "resume_run",
        "description": "Resume a paused run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    # -- Inbox tools --
    {
        "name": "list_inbox",
        "description": "List inbox items for a run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
                "status": {"type": "string"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_inbox_item",
        "description": "Get a single inbox item",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "format": "uuid"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "respond_to_inbox",
        "description": "Answer an inbox item",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "format": "uuid"},
                "answer": {"type": "string"},
            },
            "required": ["item_id", "answer"],
        },
    },
    {
        "name": "skip_inbox_item",
        "description": "Skip an inbox item",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "format": "uuid"},
            },
            "required": ["item_id"],
        },
    },
    {
        "name": "reject_inbox_item",
        "description": "Reject an inbox item",
        "inputSchema": {
            "type": "object",
            "properties": {
                "item_id": {"type": "string", "format": "uuid"},
                "reason": {"type": "string"},
            },
            "required": ["item_id", "reason"],
        },
    },
    # -- Trace tools --
    {
        "name": "query_events",
        "description": "Query events for a run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
                "event_type": {"type": "string"},
                "since": {"type": "string", "format": "date-time"},
                "limit": {"type": "integer", "default": 100},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_transcript",
        "description": "Get a session transcript",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "format": "uuid"},
            },
            "required": ["session_id"],
        },
    },
    {
        "name": "search_transcript",
        "description": "Search a transcript",
        "inputSchema": {
            "type": "object",
            "properties": {
                "session_id": {"type": "string", "format": "uuid"},
                "query": {"type": "string"},
            },
            "required": ["session_id", "query"],
        },
    },
    {
        "name": "list_tasks",
        "description": "List tasks for a run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "get_task_attempts",
        "description": "Get attempts for a task",
        "inputSchema": {
            "type": "object",
            "properties": {
                "task_id": {"type": "string", "format": "uuid"},
            },
            "required": ["task_id"],
        },
    },
    {
        "name": "get_notepad",
        "description": "Get notepad entries for a run",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    # -- Event tools --
    {
        "name": "fire_event",
        "description": "Fire a named event for wait-for tasks",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
                "event_name": {"type": "string"},
                "payload": {"type": "object"},
            },
            "required": ["run_id", "event_name"],
        },
    },
    # -- Config tools --
    {
        "name": "show_config",
        "description": "Show a run's config snapshot",
        "inputSchema": {
            "type": "object",
            "properties": {
                "run_id": {"type": "string", "format": "uuid"},
            },
            "required": ["run_id"],
        },
    },
    {
        "name": "show_pricing",
        "description": "Show the pricing table",
        "inputSchema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def get_tool_definitions() -> list[dict[str, object]]:
    return [dict(t) for t in TOOL_DEFINITIONS]
