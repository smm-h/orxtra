"""Tests for the load_tools meta-tool."""

from __future__ import annotations

from typing import Any

import pytest
from orxtra.protocols import Confirmation, Tool, ToolError, ToolOutput
from orxtra.tool._load_tools import make_load_tools_tool


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dummy_tool(name: str, desc: str = "A tool") -> Tool:
    async def _execute(args: dict[str, Any]) -> ToolOutput[str]:
        return ToolOutput(data="ok", text="ok")

    return Tool(
        name=name,
        description=desc,
        parameters={"type": "object", "properties": {"x": {"type": "string"}}},
        execute=_execute,
    )


class _ToolHolder:
    """Simulates a session's mutable tool list."""

    def __init__(self, tools: list[Tool] | None = None) -> None:
        self.tools: list[Tool] = tools or []

    def get(self) -> list[Tool]:
        return self.tools

    def set(self, tools: list[Tool]) -> None:
        self.tools = tools


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadToolsBasic:
    async def test_load_single_tool(self) -> None:
        alpha = _dummy_tool("alpha")
        registry = {"alpha": alpha, "beta": _dummy_tool("beta")}
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        result = await lt.execute({"names": ["alpha"]})
        assert "alpha" in result.text
        assert len(holder.tools) == 1
        assert holder.tools[0].name == "alpha"

    async def test_load_multiple_tools(self) -> None:
        registry = {
            "alpha": _dummy_tool("alpha"),
            "beta": _dummy_tool("beta"),
            "gamma": _dummy_tool("gamma"),
        }
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        result = await lt.execute({"names": ["alpha", "gamma"]})
        assert "2 tool(s)" in result.text
        names = [t.name for t in holder.tools]
        assert "alpha" in names
        assert "gamma" in names

    async def test_load_tool_already_present(self) -> None:
        alpha = _dummy_tool("alpha")
        registry = {"alpha": alpha}
        holder = _ToolHolder([alpha])
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        result = await lt.execute({"names": ["alpha"]})
        assert "Already loaded" in result.text
        # Should not duplicate
        assert len(holder.tools) == 1

    async def test_load_mix_new_and_existing(self) -> None:
        alpha = _dummy_tool("alpha")
        beta = _dummy_tool("beta")
        registry = {"alpha": alpha, "beta": beta}
        holder = _ToolHolder([alpha])
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        result = await lt.execute({"names": ["alpha", "beta"]})
        assert "Loaded 1 tool(s): beta" in result.text
        assert "Already loaded: alpha" in result.text
        assert len(holder.tools) == 2


class TestLoadToolsErrors:
    async def test_unknown_tool_raises(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        with pytest.raises(ToolError, match="Unknown tools: nonexistent"):
            await lt.execute({"names": ["nonexistent"]})

    async def test_empty_names_raises(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        with pytest.raises(ToolError, match="names must not be empty"):
            await lt.execute({"names": []})

    async def test_partial_unknown_raises(self) -> None:
        """If any name is unknown, error before loading any."""
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        with pytest.raises(ToolError, match="Unknown tools: missing"):
            await lt.execute({"names": ["alpha", "missing"]})
        # Nothing should have been loaded
        assert len(holder.tools) == 0


class TestLoadToolsMetadata:
    def test_tool_name_and_schema(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        assert lt.name == "load_tools"
        assert "names" in lt.parameters.get("properties", {})
        assert lt.parameters["properties"]["names"]["type"] == "array"

    async def test_result_is_confirmation(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        result = await lt.execute({"names": ["alpha"]})
        assert isinstance(result.data, Confirmation)

    async def test_preserves_existing_tools(self) -> None:
        """Loading new tools preserves existing tools in the holder."""
        existing = _dummy_tool("existing")
        new = _dummy_tool("new")
        registry = {"new": new}
        holder = _ToolHolder([existing])
        lt = make_load_tools_tool(registry, holder.get, holder.set)

        await lt.execute({"names": ["new"]})
        names = [t.name for t in holder.tools]
        assert names == ["existing", "new"]
