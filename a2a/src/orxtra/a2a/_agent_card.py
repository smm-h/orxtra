"""Agent Card generation from capability registry."""

from __future__ import annotations

from typing import TYPE_CHECKING

from a2a.types.a2a_pb2 import (
    AgentCapabilities,
    AgentCard,
    AgentInterface,
    AgentSkill,
)

if TYPE_CHECKING:
    from orxtra.a2a._skills import SkillRegistry

_DESCRIPTION = "Autonomous multi-agent AI workflow orchestration"


def build_agent_card(
    skill_registry: SkillRegistry,
    *,
    url: str,
    version: str,
    name: str = "orxtra",
    description: str = _DESCRIPTION,
) -> AgentCard:
    """Build an A2A AgentCard from the skill registry."""
    skills = [
        AgentSkill(
            id=skill.id,
            name=skill.name,
            description=skill.description,
            input_modes=list(skill.input_modes),
            output_modes=list(skill.output_modes),
        )
        for skill in skill_registry.list_skills()
    ]

    return AgentCard(
        name=name,
        description=description,
        version=version,
        supported_interfaces=[AgentInterface(url=url)],
        capabilities=AgentCapabilities(streaming=True),
        skills=skills,
    )
