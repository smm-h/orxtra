"""Packaged .md template loader for scheduler prompt strings.

All hard-coded prompt text in the scheduler lives in the prompts/
subdirectory as .md files. This module provides a thin loader
with strict variable substitution via the compose engine.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from orxtra.compose import resolve_variables

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Cache loaded templates to avoid repeated filesystem reads.
_cache: dict[str, str] = {}


def load_template(name: str) -> str:
    """Load a .md template by name (without extension).

    Returns the raw template text with trailing newline stripped.
    Caches the result for subsequent calls.
    """
    if name not in _cache:
        path = _PROMPTS_DIR / f"{name}.md"
        _cache[name] = path.read_text().rstrip("\n")
    return _cache[name]


def render_template(name: str, variables: dict[str, str]) -> str:
    """Load a .md template and apply strict variable substitution.

    Every {placeholder} in the template must have a matching variable,
    and every variable must be used. Raises ValueError on mismatch.
    """
    template = load_template(name)
    return resolve_variables(template, variables)


def render_template_lenient(
    name: str, variables: dict[str, Any],
) -> str:
    """Load a .md template and apply lenient variable substitution.

    This exists ONLY for backward compatibility during migration.
    Prefer render_template (strict) for all new code.
    """
    template = load_template(name)
    for k, v in variables.items():
        template = template.replace(f"{{{k}}}", str(v))
    return template
