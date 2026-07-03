"""Strict two-way variable substitution.

Ported from agent/_prompt.py with identical semantics:
- {variable_name} placeholders are replaced with values
- Unresolved placeholders raise ValueError
- Unused variables raise ValueError
- {include:...} syntax is not treated as a variable
"""

from __future__ import annotations

import re

_VAR_RE = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def resolve_variables(template: str, variables: dict[str, str]) -> str:
    """Apply strict two-way variable substitution.

    Every placeholder in the template must have a corresponding variable,
    and every variable must be used by at least one placeholder.
    Raises ValueError on unresolved placeholders or unused variables.
    """
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
