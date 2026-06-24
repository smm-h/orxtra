"""Resolve agent allow-lists with wildcards and tag filters."""

from __future__ import annotations


def resolve_allow_list(
    allow: list[str],
    tool_names: dict[str, tuple[str, frozenset[str]]],
) -> set[str]:
    """Resolve an allow list with wildcards and tag filters to concrete tool names.

    Supports:
    - Explicit names: "read", "write" -- exact match
    - Namespace wildcards: "fs.*" -- all tools whose namespace starts with "fs."
      (also matches "fs" exactly)
    - Tag filters: "#readonly" -- all tools tagged "readonly"
    - Universal wildcard: "*" -- all tools

    Unknown explicit names are silently ignored (matches current behavior
    where an allow-list entry for a tool that doesn't exist simply has
    no effect).

    Args:
        allow: The agent's allow list entries.
        tool_names: Mapping of tool name to (namespace, tags).

    Returns:
        Set of concrete tool names that passed the filter.
    """
    result: set[str] = set()

    for entry in allow:
        if entry == "*":
            result.update(tool_names)
        elif entry.startswith("#"):
            tag = entry[1:]
            for name, (_, tags) in tool_names.items():
                if tag in tags:
                    result.add(name)
        elif entry.endswith(".*"):
            prefix = entry[:-2]
            for name, (namespace, _) in tool_names.items():
                if namespace == prefix or namespace.startswith(f"{prefix}."):
                    result.add(name)
        elif entry in tool_names:
            result.add(entry)
        # else: unknown explicit name, silently ignored

    return result
