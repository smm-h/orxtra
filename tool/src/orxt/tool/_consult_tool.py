from __future__ import annotations

from pathlib import Path
from typing import Any
from uuid import UUID

from orxt.protocols._tool import Tool, ToolError
from orxt.tool._validation import validate_args


CONSULT_STRIP_TOOLS: frozenset[str] = frozenset({
    "write", "edit", "delete", "move", "copy", "mkdir", "set_executable",
    "exec", "git",
    "http",
    "start_task", "end_task", "create_task", "create_workflow", "create_wait_for",
})

_CONSULT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "agent": {
            "type": "string",
            "description": "Name of agent to consult.",
        },
        "question": {
            "type": "string",
            "minLength": 1,
            "description": "The question to ask.",
        },
        "variable_values": {
            "type": "object",
            "additionalProperties": {"type": "string"},
            "description": "Template variable substitutions.",
        },
    },
    "required": ["agent", "question"],
    "additionalProperties": False,
}


def make_consult_tool(
    tool_registry: dict[str, Tool],
    transport_registry: dict[str, Any],
    trace_writer: Any,
    run_id: UUID,
    read_root: Path,
    categories: dict[str, str],
    agents: dict[str, Any],
) -> Tool:
    async def _execute(args: dict[str, Any]) -> str:
        validate_args(args, _CONSULT_SCHEMA)

        agent_name = args["agent"]
        question = args["question"]

        if agent_name not in agents:
            raise ToolError(f"Unknown agent: {agent_name!r}")

        agent_def = agents[agent_name]

        # Filter out mutating tools
        filtered_tools: dict[str, Tool] = {
            name: tool
            for name, tool in tool_registry.items()
            if name not in CONSULT_STRIP_TOOLS
        }

        # Reconstruct http tool in consult_mode if the agent is allowed http
        agent_tools: list[str] = agent_def.tools
        if "http" in agent_tools and "http" in tool_registry:
            from orxt.tool._http_tool import make_http_tool

            filtered_tools["http"] = make_http_tool(
                allowed_hosts="allow_all", consult_mode=True,
            )

        # Resolve model from agent category
        resolved = categories[agent_def.category]
        provider_name, _, model_name = resolved.partition("/")
        transport = transport_registry.get(provider_name)
        if transport is None:
            msg = f"Transport for provider {provider_name!r} not found"
            raise ToolError(msg)

        response = await transport.send(
            question,
            model=model_name,
            tools=list(filtered_tools.values()),
        )

        return response.text

    return Tool(
        name="consult",
        description=(
            "Consult another agent with a read-only question. "
            "The consulted agent cannot modify files, execute commands, "
            "or manage tasks. Returns the agent's text response."
        ),
        parameters=_CONSULT_SCHEMA,
        execute=_execute,
    )
