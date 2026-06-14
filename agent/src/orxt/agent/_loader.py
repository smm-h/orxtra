from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

from orxt.agent._prompt import resolve_includes
from orxt.agent._types import Agent

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
    tools_section: dict[str, Any] = data["tools"]

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
    unknown_keys = set(tools_section.keys()) - {"allow"}
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        msg = f"Unknown keys in [tools] section: {names}"
        raise ValueError(msg)
    agent_section["allow"] = tools_section["allow"]

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
