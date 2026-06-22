from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.agent._types import Agent


def load_categories(path: Path) -> dict[str, str]:
    if not path.is_file():
        msg = f"Categories file not found: {path}"
        raise FileNotFoundError(msg)
    with path.open("rb") as f:
        data = tomllib.load(f)
    if "categories" not in data:
        msg = f"Missing [categories] section in {path}"
        raise ValueError(msg)
    categories: dict[str, str] = data["categories"]
    return categories


def resolve_category(agent: Agent, categories: dict[str, str]) -> str:
    if agent.category not in categories:
        msg = f"Unknown category: {agent.category}"
        raise ValueError(msg)
    return categories[agent.category]
