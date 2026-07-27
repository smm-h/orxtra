"""Skill registry: maps A2A skills to orxtra capabilities."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

from orxtra.a2a._gen_skill import validate_bytes as _validate_skill_document

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.protocols import Capability


class SkillValidationError(ValueError):
    """A skill document failed strictspec validation at the load boundary."""

# Namespaces excluded from A2A exposure -- same filter as MCP.
_EXCLUDED_NAMESPACES: frozenset[str] = frozenset({
    "validate",
    "dispatch",
})


@dataclass(frozen=True)
class Skill:
    """A2A skill descriptor."""

    id: str
    name: str
    description: str
    capability_name: str
    input_modes: tuple[str, ...]
    output_modes: tuple[str, ...]


class SkillRegistry:
    """Registry that maps A2A skills to orxtra capabilities.

    Can load skill definitions from TOML files in a configurable
    directory. Falls back to auto-generating skills from the
    capability registry if no TOML files exist.
    """

    def __init__(
        self,
        capabilities: list[Capability],
        skills_dir: Path | None = None,
    ) -> None:
        self._capabilities_by_name: dict[str, Capability] = {
            c.name: c for c in capabilities
        }
        self._skills: list[Skill] = self._load_or_generate(
            skills_dir,
        )

    def _load_or_generate(
        self, skills_dir: Path | None,
    ) -> list[Skill]:
        """Load from TOML or auto-generate from capabilities."""
        if skills_dir is not None and skills_dir.is_dir():
            toml_files = sorted(skills_dir.glob("*.toml"))
            if toml_files:
                return self._load_from_toml(toml_files)

        return self._generate_from_capabilities()

    def _load_from_toml(
        self, toml_files: list[Path],
    ) -> list[Skill]:
        """Load skill definitions from TOML files."""
        skills: list[Skill] = []
        for path in toml_files:
            text = path.read_text()
            # strictspec document gate: enforces integer format_version and the
            # required id/name/description/capability_name shape (formerly
            # implicit KeyErrors). Capability existence stays consumer-native below.
            _root, diags = _validate_skill_document(text.encode("utf-8"), "toml")
            if diags:
                detail = "\n".join(
                    f"  {d.code} at {d.path}: {d.message}" for d in diags
                )
                msg = f"Invalid skill document ({path}):\n{detail}"
                raise SkillValidationError(msg)
            data = tomllib.loads(text)

            capability_name = data["capability_name"]
            if capability_name not in self._capabilities_by_name:
                msg = (
                    f"Skill {data['id']!r} references "
                    f"unknown capability "
                    f"{capability_name!r} (file: {path})"
                )
                raise ValueError(msg)

            skills.append(
                Skill(
                    id=data["id"],
                    name=data["name"],
                    description=data["description"],
                    capability_name=capability_name,
                    input_modes=tuple(
                        data.get(
                            "input_modes",
                            ["text/plain", "application/json"],
                        ),
                    ),
                    output_modes=tuple(
                        data.get(
                            "output_modes",
                            ["application/json"],
                        ),
                    ),
                )
            )

        return skills

    def _generate_from_capabilities(self) -> list[Skill]:
        """Auto-generate skills from capabilities."""
        skills: list[Skill] = []
        for cap in self._capabilities_by_name.values():
            if cap.namespace in _EXCLUDED_NAMESPACES:
                continue

            skills.append(
                Skill(
                    id=cap.name,
                    name=cap.description,
                    description=cap.description,
                    capability_name=cap.name,
                    input_modes=(
                        "text/plain",
                        "application/json",
                    ),
                    output_modes=("application/json",),
                )
            )

        return skills

    def list_skills(self) -> list[Skill]:
        """Return all registered skills."""
        return list(self._skills)

    def get_skill(self, skill_id: str) -> Skill | None:
        """Look up a skill by ID."""
        for skill in self._skills:
            if skill.id == skill_id:
                return skill
        return None

    def get_capability_for_skill(
        self, skill_id: str,
    ) -> Capability | None:
        """Look up the capability bound to a skill."""
        skill = self.get_skill(skill_id)
        if skill is None:
            return None
        return self._capabilities_by_name.get(skill.capability_name)
