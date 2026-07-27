from __future__ import annotations

import tomllib
from typing import TYPE_CHECKING

from orxtra.agent._gen_categories import validate_bytes as _validate_categories_document

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.agent._types import Agent


class CategoriesValidationError(ValueError):
    """A categories document failed strictspec validation at the load boundary."""


def load_categories(path: Path) -> dict[str, str]:
    if not path.is_file():
        msg = f"Categories file not found: {path}"
        raise FileNotFoundError(msg)
    text = path.read_text()
    # strictspec document gate: enforces integer format_version and the required
    # [categories] map shape. Subsumes the hand-rolled "Missing [categories]
    # section" check. Cross-document resolution (agent.category must exist here)
    # stays consumer-native in resolve_category.
    _root, diags = _validate_categories_document(text.encode("utf-8"), "toml")
    if diags:
        detail = "\n".join(f"  {d.code} at {d.path}: {d.message}" for d in diags)
        msg = f"Invalid categories document ({path}):\n{detail}"
        raise CategoriesValidationError(msg)
    data = tomllib.loads(text)
    categories: dict[str, str] = data["categories"]
    return categories


def resolve_category(agent: Agent, categories: dict[str, str]) -> str:
    if agent.category is None:
        msg = "Agent has no category (uses explicit provider/model)"
        raise ValueError(msg)
    if agent.category not in categories:
        msg = f"Unknown category: {agent.category}"
        raise ValueError(msg)
    return categories[agent.category]
