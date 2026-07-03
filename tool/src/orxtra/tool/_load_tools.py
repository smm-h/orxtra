"""Meta-tool that lets agents request full tool schemas on demand.

When using deferred/compact tool manifests, agents start with minimal tool
specs. Calling load_tools requests full schemas for specific tools, which
are then built lazily from the registry, wrapped through the pipeline,
and added to the active session tool set.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.protocols import Confirmation, Tool, ToolError, ToolOutput
from pydantic import BaseModel

if TYPE_CHECKING:
    from collections.abc import Callable


class _LoadToolsParams(BaseModel):
    """Parameters for the load_tools meta-tool."""

    names: list[str]


def make_load_tools_tool(
    allowed_names: frozenset[str],
    build_tool: Callable[[str], Tool],
    get_session_tools: Callable[[], list[Tool]],
    set_session_tools: Callable[[list[Tool]], None],
) -> Tool:
    """Create the load_tools meta-tool.

    Args:
        allowed_names: Set of tool names the agent is allowed to load.
            Loading a name outside this set is a hard ToolError.
        build_tool: Callable that builds a fully pipeline-wrapped Tool
            by name. Called lazily when the agent requests loading.
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

        # Enforce allow-list scoping: every requested name must be
        # in the resolved allow list.  Hard error otherwise.
        disallowed = [n for n in names if n not in allowed_names]
        if disallowed:
            msg = (
                f"Tools not in allow list: "
                f"{', '.join(sorted(disallowed))}"
            )
            raise ToolError(msg)

        # Get the current tool set
        current_tools = get_session_tools()
        # Map name -> tool for checking deferred stubs.
        current_by_name = {t.name: t for t in current_tools}

        # Build and add tools that aren't already loaded.
        # A deferred stub counts as "not loaded" -- calling
        # load_tools for it replaces the stub with the
        # fully-built tool.
        to_add: list[Tool] = []
        already_loaded: list[str] = []
        for name in names:
            existing = current_by_name.get(name)
            if existing is not None and not existing.deferred:
                already_loaded.append(name)
            else:
                to_add.append(build_tool(name))

        if to_add:
            # Replace deferred stubs with fully-built tools:
            # remove any stub with the same name, then add the
            # fully-built version.
            added_names_set = {t.name for t in to_add}
            new_tools = [
                t for t in current_tools
                if t.name not in added_names_set
            ] + to_add
            set_session_tools(new_tools)

        added_names = [t.name for t in to_add]
        parts = []
        if added_names:
            parts.append(
                f"Loaded {len(added_names)} tool(s): "
                f"{', '.join(added_names)}"
            )
        if already_loaded:
            parts.append(
                f"Already loaded: {', '.join(already_loaded)}"
            )

        message = ". ".join(parts) if parts else "No tools loaded"
        result = Confirmation(message=message)
        return ToolOutput(data=result, text=message)

    return Tool(
        name="load_tools",
        description=(
            "Load full schemas for tools by name. "
            "Use this when you need a tool whose full schema "
            "is not yet available."
        ),
        parameters=_LoadToolsParams.model_json_schema(),
        execute=execute,
    )
