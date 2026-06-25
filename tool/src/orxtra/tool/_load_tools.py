"""Meta-tool that lets agents request full tool schemas on demand.

When using deferred/compact tool manifests, agents start with minimal tool
specs. Calling load_tools requests full schemas for specific tools, which
are then added to the active session tool set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

from orxtra.protocols import Confirmation, Tool, ToolError, ToolOutput

if TYPE_CHECKING:
    from collections.abc import Callable


class _LoadToolsParams(BaseModel):
    """Parameters for the load_tools meta-tool."""

    names: list[str]


def make_load_tools_tool(
    full_registry: dict[str, Tool],
    get_session_tools: Callable[[], list[Tool]],
    set_session_tools: Callable[[list[Tool]], None],
) -> Tool:
    """Create the load_tools meta-tool.

    Args:
        full_registry: All available tools with full schemas, keyed by name.
        get_session_tools: Callable that returns the current active tool list.
        set_session_tools: Callable that replaces the active tool list
            (e.g., session.update_tools).
    """

    async def execute(args: dict[str, Any]) -> ToolOutput[Confirmation]:
        validated = _LoadToolsParams.model_validate(args)
        names = validated.names

        if not names:
            msg = "names must not be empty"
            raise ToolError(msg)

        # Check for unknown tool names
        unknown = [n for n in names if n not in full_registry]
        if unknown:
            msg = f"Unknown tools: {', '.join(sorted(unknown))}"
            raise ToolError(msg)

        # Get the current tool set
        current_tools = get_session_tools()
        current_names = {t.name for t in current_tools}

        # Determine which tools to add (skip already-present ones)
        to_add = [
            full_registry[n] for n in names if n not in current_names
        ]
        already_loaded = [n for n in names if n in current_names]

        if to_add:
            new_tools = list(current_tools) + to_add
            set_session_tools(new_tools)

        added_names = [t.name for t in to_add]
        parts = []
        if added_names:
            parts.append(f"Loaded {len(added_names)} tool(s): {', '.join(added_names)}")
        if already_loaded:
            parts.append(f"Already loaded: {', '.join(already_loaded)}")

        message = ". ".join(parts) if parts else "No tools loaded"
        result = Confirmation(message=message)
        return ToolOutput(data=result, text=message)

    return Tool(
        name="load_tools",
        description=(
            "Load full schemas for tools by name. "
            "Use this when you need a tool whose full schema is not yet available."
        ),
        parameters=_LoadToolsParams.model_json_schema(),
        execute=execute,
    )
