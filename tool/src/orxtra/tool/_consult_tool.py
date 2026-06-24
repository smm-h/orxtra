from __future__ import annotations

from pathlib import Path
from typing import Any

from orxtra.protocols._results import ConsultResponse, ToolOutput
from orxtra.protocols._tool import Tool, ToolError
from orxtra.tool._decorator import tool
from orxtra.tool._params import ConsultParams
from orxtra.tool._renderers import TextRenderer

CONSULT_STRIP_TOOLS: frozenset[str] = frozenset({
    "write", "edit", "multi_edit", "delete", "move", "copy", "mkdir",
    "set_executable",
    "exec", "shell", "git",
    "http",
    "start_task", "end_task", "create_task", "create_workflow", "create_wait_for",
})


@tool(
    "consult",
    "Consult another agent with a read-only question. "
    "The consulted agent cannot modify files, execute commands, "
    "or manage tasks. Returns the agent's text response.",
    renderer=TextRenderer(),
)
async def _consult_impl(
    params: ConsultParams,
    *,
    tool_registry: dict[str, Tool],
    transport_registry: dict[str, Any],
    read_root: Path,
    categories: dict[str, str],
    agents: dict[str, Any],
) -> ToolOutput[ConsultResponse]:
    agent_name = params.agent
    question = params.question

    if agent_name not in agents:
        msg = f"Unknown agent: {agent_name!r}"
        raise ToolError(msg)

    agent_def = agents[agent_name]

    # Filter out mutating tools
    filtered_tools: dict[str, Tool] = {
        name: t
        for name, t in tool_registry.items()
        if name not in CONSULT_STRIP_TOOLS
    }

    # Reconstruct http tool in consult_mode if the agent is allowed http
    agent_tools: list[str] = agent_def.allow
    if "http" in agent_tools and "http" in tool_registry:
        from orxtra.tool._http_tool import make_http_tool  # noqa: PLC0415

        filtered_tools["http"] = make_http_tool(
            allowed_hosts="allow_all", consult_mode=True,
        )

    # Reconstruct git tool in consult mode with read-only subcommands
    if "git" in agent_tools and "git" in tool_registry:
        from orxtra.tool._git_tool import make_git_tool  # noqa: PLC0415

        filtered_tools["git"] = make_git_tool(
            read_root=read_root,
            allowed_subcommands=[
                "status", "diff", "log", "show",
                "blame", "branches", "changed_files",
            ],
        )

    # Resolve provider + model
    if (
        agent_def.provider is not None
        and agent_def.model is not None
    ):
        provider_name = agent_def.provider
        model_name = agent_def.model
    else:
        if agent_def.category is None:
            msg = (
                f"Agent '{agent_name}' has no category,"
                " provider, or model"
            )
            raise ToolError(msg)
        resolved = categories[agent_def.category]
        provider_name, _, model_name = (
            resolved.partition("/")
        )
    transport = transport_registry.get(provider_name)
    if transport is None:
        msg = f"Transport for provider {provider_name!r} not found"
        raise ToolError(msg)

    result_text = ""
    async for event in transport.send(
        question,
        model=model_name,
        system_prompt=agent_def.prompt,
        tools=list(filtered_tools.values()),
    ):
        if type(event).__name__ == "Result":
            result_text = event.text

    return ToolOutput(
        data=ConsultResponse(text=result_text, model=model_name),
        text=result_text,
    )


def make_consult_tool(  # noqa: PLR0913
    tool_registry: dict[str, Tool],
    transport_registry: dict[str, Any],
    trace_writer: Any,  # noqa: ARG001, ANN401
    run_id: Any,  # noqa: ARG001, ANN401
    read_root: Path,
    categories: dict[str, str],
    agents: dict[str, Any],
) -> Tool:
    """Create a consult tool that delegates read-only questions to other agents.

    Args:
        tool_registry: Registry of all available tools.
        transport_registry: Registry of LLM transport providers.
        trace_writer: Unused, kept for caller compatibility.
        run_id: Unused, kept for caller compatibility.
        read_root: Root directory for path containment.
        categories: Agent category -> provider/model mapping.
        agents: Agent name -> agent definition mapping.
    """
    return _consult_impl.bind(
        tool_registry=tool_registry,
        transport_registry=transport_registry,
        read_root=read_root,
        categories=categories,
        agents=agents,
    )
