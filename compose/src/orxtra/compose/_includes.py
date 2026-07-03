"""Recursive include resolution with cycle detection.

Ported from agent/_prompt.py with identical semantics:
- {include:path} directives are resolved relative to the including file
- Circular includes raise ValueError
- Missing files raise FileNotFoundError
- Nested includes are resolved recursively
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_INCLUDE_RE = re.compile(r"\{include:([^}]+)\}")


def resolve_includes(template: str, base_dir: Path) -> str:
    """Resolve all {include:path} directives in template text.

    Includes are resolved relative to base_dir. Nested includes are
    resolved relative to the including file's directory. Circular
    includes raise ValueError; missing files raise FileNotFoundError.
    """
    return _resolve_includes(template, base_dir, frozenset())


def _resolve_includes(
    template: str, base_dir: Path, seen: frozenset[Path]
) -> str:
    def replacer(match: re.Match[str]) -> str:
        rel_path = match.group(1)
        abs_path = (base_dir / rel_path).resolve()
        if abs_path in seen:
            msg = f"Circular include detected: {abs_path}"
            raise ValueError(msg)
        if not abs_path.is_file():
            msg = f"Include file not found: {abs_path}"
            raise FileNotFoundError(msg)
        content = abs_path.read_text()
        return _resolve_includes(content, abs_path.parent, seen | {abs_path})

    return _INCLUDE_RE.sub(replacer, template)
