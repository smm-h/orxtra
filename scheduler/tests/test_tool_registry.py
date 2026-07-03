"""Tests for ToolRegistry and data-driven tool construction."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock
from uuid import UUID

import pytest
import uuid6
from orxtra.protocols import Tool
from orxtra.scheduler._tool_registry import (
    Edge,
    ToolDeps,
    ToolEntry,
    ToolRegistry,
    create_builtin_registry,
)
from orxtra.write_safety import StaleWriteTracker, WriteQueue


def _make_deps(tmp_path: Path) -> ToolDeps:
    """Create a minimal ToolDeps for testing."""
    return ToolDeps(
        read_root=tmp_path,
        write_scope=None,
        write_queue=WriteQueue(),
        stale_tracker=StaleWriteTracker(),
        session_id="test-session",
        trace_writer=MagicMock(),
        run_id=uuid6.uuid7(),
        task_id=uuid6.uuid7(),
        task_name="test-task",
        task_agent="test-agent",
        scheduler_ref=MagicMock(),
        transport_registry={},
        categories={},
        agents={},
        preview_threshold=10000,
        preview_lines=50,
    )


def _make_dummy_tool(name: str) -> Tool:
    """Create a minimal Tool for testing."""
    from unittest.mock import AsyncMock  # noqa: PLC0415
    return Tool(
        name=name,
        description=f"Test tool: {name}",
        parameters={"type": "object", "properties": {}},
        execute=AsyncMock(return_value="ok"),
    )


class TestToolRegistryBasics:
    """Basic registration and retrieval."""

    def test_register_and_contains(self) -> None:
        registry = ToolRegistry()
        entry = ToolEntry(
            name="test",
            namespace="ns",
            tags=frozenset({"tag1"}),
            factory=lambda deps: _make_dummy_tool("test"),
        )
        registry.register(entry)
        assert "test" in registry
        assert len(registry) == 1

    def test_duplicate_raises(self) -> None:
        registry = ToolRegistry()
        entry = ToolEntry(
            name="test",
            namespace="ns",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("test"),
        )
        registry.register(entry)
        with pytest.raises(ValueError, match="Duplicate"):
            registry.register(entry)

    def test_get_metadata(self) -> None:
        registry = ToolRegistry()
        entry = ToolEntry(
            name="test",
            namespace="ns.sub",
            tags=frozenset({"tag1", "tag2"}),
            factory=lambda deps: _make_dummy_tool("test"),
        )
        registry.register(entry)
        meta = registry.get_metadata()
        assert meta == {
            "test": ("ns.sub", frozenset({"tag1", "tag2"})),
        }


class TestBuildTools:
    """Building tools from resolved names."""

    def test_build_selected(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        for name in ("a", "b", "c"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _make_dummy_tool(n),
            ))
        deps = _make_deps(tmp_path)
        tools = registry.build_tools({"a", "c"}, deps)
        names = {t.name for t in tools}
        assert names == {"a", "c"}

    def test_build_unknown_names_skipped(
        self, tmp_path: Path,
    ) -> None:
        registry = ToolRegistry()
        registry.register(ToolEntry(
            name="known",
            namespace="test",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("known"),
        ))
        deps = _make_deps(tmp_path)
        tools = registry.build_tools(
            {"known", "unknown"}, deps,
        )
        assert len(tools) == 1
        assert tools[0].name == "known"

    def test_build_empty_set(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        registry.register(ToolEntry(
            name="tool",
            namespace="test",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("tool"),
        ))
        deps = _make_deps(tmp_path)
        tools = registry.build_tools(set(), deps)
        assert tools == []


class TestRegisterCustom:
    """Custom tool registration with full metadata."""

    def test_custom_tool(self, tmp_path: Path) -> None:
        registry = ToolRegistry()
        registry.register_custom(
            "custom",
            namespace="custom.test",
            tags=frozenset({"readonly"}),
            factory=lambda deps: _make_dummy_tool("custom"),
        )
        assert "custom" in registry

        meta = registry.get_metadata()
        assert meta["custom"] == (
            "custom.test", frozenset({"readonly"}),
        )

        deps = _make_deps(tmp_path)
        tools = registry.build_tools({"custom"}, deps)
        assert len(tools) == 1
        assert tools[0].name == "custom"

    def test_custom_duplicate_raises(self) -> None:
        registry = ToolRegistry()
        registry.register_custom(
            "x",
            namespace="custom.x",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("x"),
        )
        with pytest.raises(ValueError, match="Duplicate"):
            registry.register_custom(
                "x",
                namespace="custom.x",
                tags=frozenset(),
                factory=lambda deps: _make_dummy_tool("x"),
            )


class TestBuiltinRegistry:
    """The pre-populated builtin registry."""

    def test_has_expected_tools(self) -> None:
        registry = create_builtin_registry()
        expected = {
            "read", "list_dir", "glob", "grep", "stat", "diff",
            "write", "edit", "multi_edit", "mkdir", "move",
            "copy", "delete", "set_executable",
            "notepad", "http",
        }
        for name in expected:
            assert name in registry, f"Missing: {name}"

    def test_does_not_include_special_tools(self) -> None:
        registry = create_builtin_registry()
        # git, consult, exec, shell, lifecycle are NOT
        # registered in the builtin registry.
        for name in ("git", "consult", "shell"):
            assert name not in registry

    def test_metadata_namespaces(self) -> None:
        registry = create_builtin_registry()
        meta = registry.get_metadata()
        assert meta["read"][0] == "fs.read"
        assert meta["write"][0] == "fs.write"
        assert meta["notepad"][0] == "io.notepad"
        assert meta["http"][0] == "io.http"

    def test_metadata_tags(self) -> None:
        registry = create_builtin_registry()
        meta = registry.get_metadata()
        assert "readonly" in meta["read"][1]
        assert "mutation" in meta["write"][1]
        assert "mutation" in meta["notepad"][1]
        assert "readonly" in meta["http"][1]
        assert "mutation" in meta["http"][1]

    def test_build_read_tool(self, tmp_path: Path) -> None:
        registry = create_builtin_registry()
        deps = _make_deps(tmp_path)
        tools = registry.build_tools({"read"}, deps)
        assert len(tools) == 1
        assert tools[0].name == "read"

    def test_build_all_tools(self, tmp_path: Path) -> None:
        registry = create_builtin_registry()
        deps = _make_deps(tmp_path)
        all_names = set(registry.get_metadata().keys())
        tools = registry.build_tools(all_names, deps)
        built_names = {t.name for t in tools}
        assert built_names == all_names

    def test_count(self) -> None:
        registry = create_builtin_registry()
        # 6 read + 8 write + notepad + http = 16
        assert len(registry) == 16


# ---------------------------------------------------------------
# Edge model
# ---------------------------------------------------------------


class TestEdgeModel:
    """Advisory edge storage and retrieval."""

    def test_add_edge(self) -> None:
        registry = ToolRegistry()
        registry.register(ToolEntry(
            name="a",
            namespace="test",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("a"),
        ))
        registry.register(ToolEntry(
            name="b",
            namespace="test",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("b"),
        ))
        registry.add_edge("a", "b", "follows")
        edges = registry.edges_from("a")
        assert len(edges) == 1
        assert edges[0].source_tool == "a"
        assert edges[0].target_tool == "b"
        assert edges[0].edge_type == "follows"
        assert edges[0].advisory is True

    def test_edges_from(self) -> None:
        registry = ToolRegistry()
        for name in ("a", "b", "c"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _make_dummy_tool(n),
            ))
        registry.add_edge("a", "b", "follows")
        registry.add_edge("a", "c", "related_to")
        edges = registry.edges_from("a")
        assert len(edges) == 2
        targets = {e.target_tool for e in edges}
        assert targets == {"b", "c"}

    def test_edges_from_empty(self) -> None:
        registry = ToolRegistry()
        registry.register(ToolEntry(
            name="a",
            namespace="test",
            tags=frozenset(),
            factory=lambda deps: _make_dummy_tool("a"),
        ))
        assert registry.edges_from("a") == []
        assert registry.edges_from("nonexistent") == []

    def test_edges_to(self) -> None:
        registry = ToolRegistry()
        for name in ("a", "b", "c"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _make_dummy_tool(n),
            ))
        registry.add_edge("a", "c", "follows")
        registry.add_edge("b", "c", "related_to")
        edges = registry.edges_to("c")
        assert len(edges) == 2
        sources = {e.source_tool for e in edges}
        assert sources == {"a", "b"}

    def test_edges_to_empty(self) -> None:
        registry = ToolRegistry()
        assert registry.edges_to("nonexistent") == []

    def test_edges_by_type(self) -> None:
        registry = ToolRegistry()
        for name in ("a", "b", "c"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _make_dummy_tool(n),
            ))
        registry.add_edge("a", "b", "follows")
        registry.add_edge("a", "c", "follows")
        registry.add_edge("b", "c", "related_to")
        follows = registry.edges_by_type("follows")
        assert len(follows) == 2
        related = registry.edges_by_type("related_to")
        assert len(related) == 1

    def test_edges_by_type_empty(self) -> None:
        registry = ToolRegistry()
        assert registry.edges_by_type("nonexistent") == []

    def test_duplicate_edge_ignored(self) -> None:
        registry = ToolRegistry()
        for name in ("a", "b"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _make_dummy_tool(n),
            ))
        registry.add_edge("a", "b", "follows")
        registry.add_edge("a", "b", "follows")
        assert len(registry.edges_from("a")) == 1

    def test_same_pair_different_types(self) -> None:
        """Same source-target pair with different edge types
        are stored independently."""
        registry = ToolRegistry()
        for name in ("a", "b"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _make_dummy_tool(n),
            ))
        registry.add_edge("a", "b", "follows")
        registry.add_edge("a", "b", "related_to")
        edges = registry.edges_from("a")
        assert len(edges) == 2
        types = {e.edge_type for e in edges}
        assert types == {"follows", "related_to"}

    def test_edge_frozen(self) -> None:
        edge = Edge(
            source_tool="a",
            target_tool="b",
            edge_type="follows",
        )
        with pytest.raises(AttributeError):
            edge.source_tool = "c"  # type: ignore[misc]


class TestBuiltinEdges:
    """Builtin registry is seeded with advisory edges."""

    def test_read_follows_edit(self) -> None:
        registry = create_builtin_registry()
        edges = registry.edges_from("read")
        targets = {e.target_tool for e in edges}
        assert "edit" in targets

    def test_grep_follows_read(self) -> None:
        registry = create_builtin_registry()
        edges = registry.edges_from("grep")
        targets = {e.target_tool for e in edges}
        assert "read" in targets

    def test_write_related_to_read(self) -> None:
        registry = create_builtin_registry()
        edges = registry.edges_from("write")
        targets = {e.target_tool for e in edges}
        assert "read" in targets
        # Verify edge type.
        related = [
            e for e in registry.edges_from("write")
            if e.target_tool == "read"
        ]
        assert related[0].edge_type == "related_to"

    def test_all_edges_advisory(self) -> None:
        registry = create_builtin_registry()
        for tool_name in registry.get_metadata():
            for edge in registry.edges_from(tool_name):
                assert edge.advisory is True
