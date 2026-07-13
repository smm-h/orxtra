"""Schema compatibility tests.

Verifies that the capability params models produce JSON schemas
structurally equivalent to the hand-written MCP tool definitions.
"""

from __future__ import annotations

from typing import Any

import pytest
from orxtra.mcp._server import get_tool_definitions
from orxtra.services._registry import get_capabilities


def _normalize_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize a pydantic JSON schema to match the MCP hand-written format.

    Strips pydantic-specific keys (title, additionalProperties, description
    on individual properties) and normalizes nullable types from
    ``anyOf: [{type: X}, {type: null}]`` to ``{type: X}``.
    """
    result: dict[str, Any] = {"type": "object"}

    props = schema.get("properties", {})
    normalized_props: dict[str, Any] = {}
    required: list[str] = list(schema.get("required", []))

    for name, prop in props.items():
        normalized_prop: dict[str, Any] = {}

        # Handle anyOf (pydantic's way of expressing Optional[T])
        if "anyOf" in prop:
            # Extract the non-null type
            non_null = [t for t in prop["anyOf"] if t.get("type") != "null"]
            if len(non_null) == 1:
                normalized_prop.update(
                    {k: v for k, v in non_null[0].items() if k != "title"},
                )
            else:
                # Multiple non-null types -- keep as-is
                normalized_prop["anyOf"] = non_null
        else:
            for k, v in prop.items():
                if k in ("title", "description"):
                    continue
                normalized_prop[k] = v

        # Copy format from the original property if present at top level
        if "format" in prop and "format" not in normalized_prop:
            normalized_prop["format"] = prop["format"]

        normalized_props[name] = normalized_prop

    result["properties"] = normalized_props
    result["required"] = sorted(required)

    return result


def _normalize_mcp_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Normalize an MCP tool schema for comparison."""
    result: dict[str, Any] = {"type": "object"}
    result["properties"] = dict(schema.get("properties", {}))
    result["required"] = sorted(schema.get("required", []))
    return result


def _get_mcp_tool_by_name(name: str) -> dict[str, Any] | None:
    """Find an MCP tool definition by name."""
    for tool in get_tool_definitions():
        if tool["name"] == name:
            return tool
    return None


# These MCP tools have matching capabilities (same name)
_MCP_CAPABILITY_NAMES: set[str] = {
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
    "create_principal",
    "get_principal",
    "list_principals",
    "delete_principal",
}


@pytest.mark.parametrize("tool_name", sorted(_MCP_CAPABILITY_NAMES))
def test_capability_schema_matches_mcp_tool(tool_name: str) -> None:
    """Each MCP tool schema must be structurally equivalent to the
    capability's params model schema."""
    # Find the MCP tool
    mcp_tool = _get_mcp_tool_by_name(tool_name)
    assert mcp_tool is not None, f"MCP tool {tool_name!r} not found"

    # Find the matching capability
    caps = {c.name: c for c in get_capabilities()}
    cap = caps.get(tool_name)
    assert cap is not None, f"Capability {tool_name!r} not found"

    # Compare schemas
    mcp_schema = _normalize_mcp_schema(mcp_tool["inputSchema"])
    cap_schema = _normalize_schema(cap.params_model.model_json_schema())

    # Same properties
    assert set(mcp_schema["properties"].keys()) == set(cap_schema["properties"].keys()), (
        f"{tool_name}: property mismatch. "
        f"MCP has {set(mcp_schema['properties'].keys())}, "
        f"capability has {set(cap_schema['properties'].keys())}"
    )

    # Same required fields
    assert mcp_schema["required"] == cap_schema["required"], (
        f"{tool_name}: required mismatch. "
        f"MCP has {mcp_schema['required']}, "
        f"capability has {cap_schema['required']}"
    )

    # Property types match
    for prop_name in mcp_schema["properties"]:
        mcp_prop = mcp_schema["properties"][prop_name]
        cap_prop = cap_schema["properties"][prop_name]

        # Compare type
        if "type" in mcp_prop:
            assert "type" in cap_prop, (
                f"{tool_name}.{prop_name}: MCP has type={mcp_prop['type']}, "
                f"capability has no type"
            )
            assert mcp_prop["type"] == cap_prop["type"], (
                f"{tool_name}.{prop_name}: type mismatch. "
                f"MCP={mcp_prop['type']}, cap={cap_prop['type']}"
            )

        # Compare format
        if "format" in mcp_prop:
            assert cap_prop.get("format") == mcp_prop["format"], (
                f"{tool_name}.{prop_name}: format mismatch. "
                f"MCP={mcp_prop.get('format')}, cap={cap_prop.get('format')}"
            )

        # Compare default
        if "default" in mcp_prop:
            assert cap_prop.get("default") == mcp_prop["default"], (
                f"{tool_name}.{prop_name}: default mismatch. "
                f"MCP={mcp_prop.get('default')}, cap={cap_prop.get('default')}"
            )


def test_all_mcp_tools_have_capabilities() -> None:
    """Every MCP tool should have a corresponding capability."""
    cap_names = {c.name for c in get_capabilities()}
    mcp_names = {str(t["name"]) for t in get_tool_definitions()}
    missing = mcp_names - cap_names
    assert missing == set(), f"MCP tools without capabilities: {missing}"


def test_capability_descriptions_match_mcp() -> None:
    """Capability descriptions should match MCP tool descriptions."""
    caps = {c.name: c for c in get_capabilities()}
    for tool in get_tool_definitions():
        name = str(tool["name"])
        if name in caps:
            assert caps[name].description == str(tool["description"]), (
                f"{name}: description mismatch"
            )
