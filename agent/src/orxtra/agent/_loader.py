from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING, Any

from orxtra.agent._gen_agent import validate_bytes as _validate_agent_document
from orxtra.agent._types import Agent, InlineToolDefinition
from orxtra.compose import resolve_includes

if TYPE_CHECKING:
    from pathlib import Path


class AgentValidationError(ValueError):
    """An agent document failed strictspec validation at the load boundary."""


def load_agent(path: Path) -> Agent:
    if not path.is_file():
        msg = f"Agent file not found: {path}"
        raise FileNotFoundError(msg)
    text = path.read_text()
    # strictspec document gate: enforces integer format_version, the [agent] and
    # [tools] shape (required name/description/prompt, required tools.allow),
    # unknown-key rejection in every section, inline-tool [[tools.define]] shape
    # (required name/description/namespace/deferred), and the category-vs-
    # provider/model routing constraints. Subsumes the hand-rolled "Missing
    # [tools]"/"Missing 'allow'"/unknown-[tools]-keys/define-missing-name checks.
    # Prompt-file resolution, include resolution, and inline-tool name uniqueness
    # stay consumer-native below.
    _root, diags = _validate_agent_document(text.encode("utf-8"), "toml")
    if diags:
        detail = "\n".join(f"  {d.code} at {d.path}: {d.message}" for d in diags)
        msg = f"Invalid agent document ({path}):\n{detail}"
        raise AgentValidationError(msg)
    data = tomllib.loads(text)

    agent_section: dict[str, Any] = dict(data.get("agent", {}))
    tools_section: dict[str, Any] = dict(data["tools"])

    prompt_rel = agent_section.pop("prompt", "")
    prompt_path = (path.parent / prompt_rel).resolve()
    if not prompt_path.is_file():
        msg = f"Prompt file not found: {prompt_path}"
        raise FileNotFoundError(msg)
    prompt_text = prompt_path.read_text()
    prompt_text = resolve_includes(prompt_text, prompt_path.parent)

    agent_section["prompt"] = prompt_text

    # [tools] shape (allow present, no unknown keys) is enforced by the strictspec
    # gate above; here we only project the validated sections onto the model.
    allow = tools_section.pop("allow")
    deferred: list[str] = tools_section.pop("deferred", [])
    define_blocks: list[dict[str, Any]] = tools_section.pop("define", [])

    agent_section["allow"] = allow
    if deferred:
        agent_section["deferred"] = deferred

    # Parse [[tools.define]] blocks into InlineToolDefinition objects.
    inline_tools: list[InlineToolDefinition] = []
    seen_names: set[str] = set()
    for block in define_blocks:
        # The gate guarantees each define block carries a 'name'; only the
        # cross-block uniqueness check remains consumer-native.
        tool_name = block["name"]
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
