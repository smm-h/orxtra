"""Auto-discovery of Tool instances in a module."""

from __future__ import annotations

from types import ModuleType

from orxtra.protocols._tool import Tool


def collect_tools(module: ModuleType) -> dict[str, Tool]:
    """Scan a module for Tool instances and return them keyed by name.

    Looks for module-level attributes that are ``Tool`` instances.
    Returns ``{tool.name: tool}`` for each one found.
    """
    result: dict[str, Tool] = {}
    for attr_name in dir(module):
        obj = getattr(module, attr_name)
        if isinstance(obj, Tool):
            result[obj.name] = obj
    return result
