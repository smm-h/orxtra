from __future__ import annotations

from typing import Any

from orxtra.mcp._tools import get_tool_definitions

EXPECTED_TOOL_NAMES: set[str] = {
    "start_run",
    "list_runs",
    "get_run",
    "abort_run",
    "pause_run",
    "resume_run",
    "list_inbox",
    "get_inbox_item",
    "respond_to_inbox",
    "skip_inbox_item",
    "reject_inbox_item",
    "query_events",
    "get_transcript",
    "search_transcript",
    "list_tasks",
    "get_task_attempts",
    "get_notepad",
    "fire_event",
    "show_config",
    "show_pricing",
}

VALIDATION_TOOL_NAMES: set[str] = {
    "validate_agent",
    "validate_categories",
    "validate_workflow",
}


def test_get_tool_definitions_returns_list() -> None:
    tools = get_tool_definitions()
    assert isinstance(tools, list)
    for tool in tools:
        assert isinstance(tool, dict)


def test_each_tool_has_required_keys() -> None:
    tools = get_tool_definitions()
    for tool in tools:
        assert "name" in tool, f"Tool missing 'name': {tool}"
        assert "description" in tool, f"Tool missing 'description': {tool}"
        assert "inputSchema" in tool, f"Tool missing 'inputSchema': {tool}"


def test_tool_count_matches_spec() -> None:
    tools = get_tool_definitions()
    assert len(tools) == 20


def test_no_validation_tools_present() -> None:
    tools = get_tool_definitions()
    names = {str(t["name"]) for t in tools}
    overlap = names & VALIDATION_TOOL_NAMES
    assert overlap == set(), f"Validation tools should not be present: {overlap}"


def test_tool_names_match_expected() -> None:
    tools = get_tool_definitions()
    names = {str(t["name"]) for t in tools}
    assert names == EXPECTED_TOOL_NAMES


def test_input_schema_has_properties_and_required() -> None:
    tools = get_tool_definitions()
    for tool in tools:
        schema: Any = tool["inputSchema"]
        assert isinstance(schema, dict), f"{tool['name']}: inputSchema is not a dict"
        assert schema.get("type") == "object", f"{tool['name']}: missing type"
        assert "properties" in schema, f"{tool['name']}: missing properties"
        assert "required" in schema, f"{tool['name']}: missing required"


def test_start_run_schema() -> None:
    tools = get_tool_definitions()
    tool = _find_tool(tools, "start_run")
    schema: Any = tool["inputSchema"]
    props: Any = schema["properties"]
    required: list[str] = schema["required"]
    assert "config_path" in props
    assert "intent" in props
    assert props["config_path"]["type"] == "string"
    assert props["intent"]["type"] == "string"
    assert "config_path" in required
    assert "intent" in required


def test_fire_event_schema() -> None:
    tools = get_tool_definitions()
    tool = _find_tool(tools, "fire_event")
    schema: Any = tool["inputSchema"]
    props: Any = schema["properties"]
    required: list[str] = schema["required"]
    assert "run_id" in required
    assert "event_name" in required
    assert "payload" not in required
    assert props["payload"]["type"] == "object"


def test_list_inbox_schema() -> None:
    tools = get_tool_definitions()
    tool = _find_tool(tools, "list_inbox")
    schema: Any = tool["inputSchema"]
    props: Any = schema["properties"]
    required: list[str] = schema["required"]
    assert "run_id" in required
    assert "status" not in required
    assert "status" in props


def test_tools_with_no_params() -> None:
    tools = get_tool_definitions()
    for name in ("list_runs", "show_pricing"):
        tool = _find_tool(tools, name)
        schema: Any = tool["inputSchema"]
        assert schema["properties"] == {}, f"{name}: expected empty properties"
        assert schema["required"] == [], f"{name}: expected empty required"


def test_query_events_schema() -> None:
    tools = get_tool_definitions()
    tool = _find_tool(tools, "query_events")
    schema: Any = tool["inputSchema"]
    props: Any = schema["properties"]
    required: list[str] = schema["required"]
    assert "run_id" in required
    assert "event_type" not in required
    assert "since" not in required
    assert "limit" not in required
    assert props["limit"]["default"] == 100


def test_tool_definitions_returns_copy() -> None:
    first = get_tool_definitions()
    second = get_tool_definitions()
    assert first == second
    assert first is not second


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _find_tool(tools: list[dict[str, object]], name: str) -> dict[str, Any]:
    for tool in tools:
        if tool["name"] == name:
            return tool  # type: ignore[return-value]
    msg = f"Tool {name!r} not found"
    raise AssertionError(msg)
