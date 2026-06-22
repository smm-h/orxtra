"""Tests for tool auto-discovery via collect_tools."""

from __future__ import annotations

from types import ModuleType
from unittest.mock import AsyncMock

from orxtra.protocols._tool import Tool
from orxtra.tool._discovery import collect_tools


def _make_tool(name: str) -> Tool:
    """Create a minimal Tool instance for testing."""
    return Tool(
        name=name,
        description=f"Tool: {name}",
        parameters={"type": "object", "properties": {}},
        execute=AsyncMock(return_value="ok"),
    )


def _make_module(**attrs: object) -> ModuleType:
    """Create a synthetic module with the given attributes."""
    mod = ModuleType("test_module")
    for key, value in attrs.items():
        setattr(mod, key, value)
    return mod


class TestFindsToolInstances:
    """collect_tools finds Tool instances in a module."""

    def test_single_tool(self) -> None:
        tool_a = _make_tool("alpha")
        mod = _make_module(my_tool=tool_a)
        result = collect_tools(mod)
        assert result == {"alpha": tool_a}

    def test_multiple_tools(self) -> None:
        tool_a = _make_tool("alpha")
        tool_b = _make_tool("beta")
        mod = _make_module(
            first=tool_a,
            second=tool_b,
        )
        result = collect_tools(mod)
        assert result == {"alpha": tool_a, "beta": tool_b}


class TestIgnoresNonToolAttributes:
    """collect_tools ignores non-Tool attributes."""

    def test_non_tools_skipped(self) -> None:
        tool_a = _make_tool("alpha")
        mod = _make_module(
            my_tool=tool_a,
            some_string="hello",
            some_int=42,
            some_list=[1, 2, 3],
            some_dict={"key": "value"},
        )
        result = collect_tools(mod)
        assert result == {"alpha": tool_a}


class TestEmptyModule:
    """collect_tools returns empty dict for module with no tools."""

    def test_no_tools(self) -> None:
        mod = _make_module(
            some_string="hello",
            some_int=42,
        )
        result = collect_tools(mod)
        assert result == {}

    def test_bare_module(self) -> None:
        mod = ModuleType("empty")
        result = collect_tools(mod)
        assert result == {}


class TestHandlesNameCorrectly:
    """collect_tools keys by tool.name, not the attribute name."""

    def test_key_is_tool_name(self) -> None:
        tool = _make_tool("real_name")
        mod = _make_module(attr_name=tool)
        result = collect_tools(mod)
        # Keyed by tool.name ("real_name"), not the
        # attribute name ("attr_name")
        assert "real_name" in result
        assert "attr_name" not in result
