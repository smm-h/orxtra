"""Tests for the load_tools meta-tool.

Tests the factory-based lazy loading with allow-list enforcement.
"""

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


def _make_build_tool(
    registry: dict[str, Tool],
) -> Any:
    """Create a build_tool callable from a dict of pre-built tools."""

    def build_tool(name: str) -> Tool:
        if name not in registry:
            msg = f"Unknown tool: {name}"
            raise ToolError(msg)
        return registry[name]

    return build_tool


def _make_lt(
    registry: dict[str, Tool],
    holder: _ToolHolder,
    allowed: frozenset[str] | None = None,
) -> Tool:
    """Shorthand for constructing load_tools with sensible defaults."""
    if allowed is None:
        allowed = frozenset(registry.keys())
    return make_load_tools_tool(
        allowed_names=allowed,
        build_tool=_make_build_tool(registry),
        get_session_tools=holder.get,
        set_session_tools=holder.set,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestLoadToolsBasic:
    async def test_load_single_tool(self) -> None:
        alpha = _dummy_tool("alpha")
        registry = {"alpha": alpha, "beta": _dummy_tool("beta")}
        holder = _ToolHolder()
        lt = _make_lt(registry, holder)

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
        lt = _make_lt(registry, holder)

        result = await lt.execute({"names": ["alpha", "gamma"]})
        assert "2 tool(s)" in result.text
        names = [t.name for t in holder.tools]
        assert "alpha" in names
        assert "gamma" in names

    async def test_load_tool_already_present(self) -> None:
        alpha = _dummy_tool("alpha")
        registry = {"alpha": alpha}
        holder = _ToolHolder([alpha])
        lt = _make_lt(registry, holder)

        result = await lt.execute({"names": ["alpha"]})
        assert "Already loaded" in result.text
        # Should not duplicate
        assert len(holder.tools) == 1

    async def test_load_mix_new_and_existing(self) -> None:
        alpha = _dummy_tool("alpha")
        beta = _dummy_tool("beta")
        registry = {"alpha": alpha, "beta": beta}
        holder = _ToolHolder([alpha])
        lt = _make_lt(registry, holder)

        result = await lt.execute({"names": ["alpha", "beta"]})
        assert "Loaded 1 tool(s): beta" in result.text
        assert "Already loaded: alpha" in result.text
        assert len(holder.tools) == 2


class TestLoadToolsErrors:
    async def test_outside_allow_list_raises(self) -> None:
        """Loading a tool not in the allow list is a hard error."""
        alpha = _dummy_tool("alpha")
        beta = _dummy_tool("beta")
        registry = {"alpha": alpha, "beta": beta}
        holder = _ToolHolder()
        # Only alpha is allowed.
        lt = _make_lt(
            registry, holder,
            allowed=frozenset({"alpha"}),
        )

        with pytest.raises(ToolError, match=r"not in allow list.*beta"):
            await lt.execute({"names": ["beta"]})

    async def test_empty_names_raises(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = _make_lt(registry, holder)

        with pytest.raises(ToolError, match="names must not be empty"):
            await lt.execute({"names": []})

    async def test_partial_disallowed_raises(self) -> None:
        """If any name is outside allow list, error before loading."""
        alpha = _dummy_tool("alpha")
        beta = _dummy_tool("beta")
        registry = {"alpha": alpha, "beta": beta}
        holder = _ToolHolder()
        lt = _make_lt(
            registry, holder,
            allowed=frozenset({"alpha"}),
        )

        with pytest.raises(ToolError, match=r"not in allow list.*beta"):
            await lt.execute({"names": ["alpha", "beta"]})
        # Nothing should have been loaded
        assert len(holder.tools) == 0


class TestLoadToolsStubReplacement:
    async def test_replaces_deferred_stub(self) -> None:
        """When a deferred stub exists in the session, loading the
        full tool replaces the stub (not duplicated)."""
        alpha_full = _dummy_tool("alpha", desc="Full alpha")
        # Simulate a deferred stub in the session.
        alpha_stub = Tool(
            name="alpha",
            description="Deferred stub",
            parameters={"type": "object", "properties": {}},
            execute=alpha_full.execute,
            deferred=True,
        )
        registry = {"alpha": alpha_full}
        holder = _ToolHolder([alpha_stub])
        lt = _make_lt(registry, holder)

        result = await lt.execute({"names": ["alpha"]})
        assert "Loaded 1 tool(s): alpha" in result.text
        assert len(holder.tools) == 1
        # The stub was replaced with the full tool.
        assert holder.tools[0].description == "Full alpha"
        assert holder.tools[0].deferred is False


class TestLoadToolsMetadata:
    def test_tool_name_and_schema(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = _make_lt(registry, holder)

        assert lt.name == "load_tools"
        assert "names" in lt.parameters.get("properties", {})
        assert lt.parameters["properties"]["names"]["type"] == "array"

    async def test_result_is_confirmation(self) -> None:
        registry = {"alpha": _dummy_tool("alpha")}
        holder = _ToolHolder()
        lt = _make_lt(registry, holder)

        result = await lt.execute({"names": ["alpha"]})
        assert isinstance(result.data, Confirmation)

    async def test_preserves_existing_tools(self) -> None:
        """Loading new tools preserves existing tools in the holder."""
        existing = _dummy_tool("existing")
        new = _dummy_tool("new")
        registry = {"new": new}
        holder = _ToolHolder([existing])
        lt = _make_lt(registry, holder)

        await lt.execute({"names": ["new"]})
        names = [t.name for t in holder.tools]
        assert names == ["existing", "new"]
