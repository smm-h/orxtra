from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

_INCLUDE_RE = re.compile(r"\{include:([^}]+)\}")
_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def resolve_includes(template: str, base_dir: Path) -> str:
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


def resolve_prompt(template: str, variables: dict[str, str]) -> str:
    placeholders = set(_VAR_RE.findall(template))
    var_keys = set(variables.keys())

    unresolved = placeholders - var_keys
    if unresolved:
        name = sorted(unresolved)[0]
        msg = f"Unresolved placeholder: {{{name}}}"
        raise ValueError(msg)

    unused = var_keys - placeholders
    if unused:
        name = sorted(unused)[0]
        msg = f"Unused variable: {name}"
        raise ValueError(msg)

    if not placeholders:
        return template

    def replacer(match: re.Match[str]) -> str:
        return variables[match.group(1)]

    return _VAR_RE.sub(replacer, template)
