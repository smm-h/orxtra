from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

from orxtra.agent._prompt import resolve_includes
from orxtra.agent._types import Agent, InlineToolDefinition

if TYPE_CHECKING:
    from pathlib import Path


def load_agent(path: Path) -> Agent:
    if not path.is_file():
        msg = f"Agent file not found: {path}"
        raise FileNotFoundError(msg)
    with path.open("rb") as f:
        data = tomllib.load(f)

    agent_section: dict[str, Any] = dict(data.get("agent", {}))
    if "tools" not in data:
        msg = f"Missing [tools] section in {path}"
        raise ValueError(msg)
    tools_section: dict[str, Any] = dict(data["tools"])

    prompt_rel = agent_section.pop("prompt", "")
    prompt_path = (path.parent / prompt_rel).resolve()
    if not prompt_path.is_file():
        msg = f"Prompt file not found: {prompt_path}"
        raise FileNotFoundError(msg)
    prompt_text = prompt_path.read_text()
    prompt_text = resolve_includes(prompt_text, prompt_path.parent)

    agent_section["prompt"] = prompt_text
    if "allow" not in tools_section:
        msg = f"Missing 'allow' key in [tools] section in {path}"
        raise ValueError(msg)

    # Extract known keys from [tools].
    allow = tools_section.pop("allow")
    define_blocks: list[dict[str, Any]] = tools_section.pop("define", [])

    unknown_keys = set(tools_section.keys())
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        msg = f"Unknown keys in [tools] section: {names}"
        raise ValueError(msg)

    agent_section["allow"] = allow

    # Parse [[tools.define]] blocks into InlineToolDefinition objects.
    inline_tools: list[InlineToolDefinition] = []
    seen_names: set[str] = set()
    for i, block in enumerate(define_blocks):
        tool_name = block.get("name")
        if tool_name is None:
            msg = (
                f"[[tools.define]] entry {i} in {path} "
                f"is missing required 'name' key"
            )
            raise ValueError(msg)
        if tool_name in seen_names:
            msg = (
                f"Duplicate inline tool name {tool_name!r} "
                f"in {path}"
            )
            raise ValueError(msg)
        seen_names.add(tool_name)
        inline_tools.append(InlineToolDefinition(**block))
    agent_section["inline_tools"] = inline_tools

    return Agent(**agent_section)


def load_agents(directory: Path) -> dict[str, Agent]:
    agents: dict[str, Agent] = {}
    for toml_path in sorted(directory.glob("*.toml")):
        agent = load_agent(toml_path)
        if agent.name in agents:
            msg = f"Duplicate agent name: {agent.name}"
            raise ValueError(msg)
        agents[agent.name] = agent
    return agents
