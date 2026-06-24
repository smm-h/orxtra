"""Tests for allow-list resolver with namespace wildcards and tag filters."""

from __future__ import annotations

import pytest

from orxtra.scheduler._allow_resolver import resolve_allow_list

# A representative tool registry for testing.
# Keys are tool names, values are (namespace, tags).
TOOL_NAMES: dict[str, tuple[str, frozenset[str]]] = {
    "read": ("fs.read", frozenset({"readonly"})),
    "list_dir": ("fs.read", frozenset({"readonly"})),
    "glob": ("fs.read", frozenset({"readonly"})),
    "grep": ("fs.read", frozenset({"readonly"})),
    "stat": ("fs.read", frozenset({"readonly"})),
    "diff": ("fs.read", frozenset({"readonly"})),
    "write": ("fs.write", frozenset({"mutation"})),
    "edit": ("fs.write", frozenset({"mutation"})),
    "multi_edit": ("fs.write", frozenset({"mutation"})),
    "mkdir": ("fs.write", frozenset({"mutation"})),
    "move": ("fs.write", frozenset({"mutation"})),
    "copy": ("fs.write", frozenset({"mutation"})),
    "delete": ("fs.write", frozenset({"mutation"})),
    "set_executable": ("fs.write", frozenset({"mutation"})),
    "git": ("git", frozenset({"readonly", "mutation"})),
    "notepad": ("io.notepad", frozenset({"mutation"})),
    "http": ("io.http", frozenset({"readonly", "mutation"})),
    "consult": ("meta.consult", frozenset({"readonly"})),
}


class TestExplicitNames:
    """Explicit names resolve to themselves."""

    def test_single_name(self) -> None:
        result = resolve_allow_list(["read"], TOOL_NAMES)
        assert result == {"read"}

    def test_multiple_names(self) -> None:
        result = resolve_allow_list(
            ["read", "write", "git"], TOOL_NAMES,
        )
        assert result == {"read", "write", "git"}


class TestNamespaceWildcards:
    """Namespace wildcards resolve to all matching tools."""

    def test_fs_wildcard_all(self) -> None:
        result = resolve_allow_list(["fs.*"], TOOL_NAMES)
        expected = {
            "read", "list_dir", "glob", "grep", "stat", "diff",
            "write", "edit", "multi_edit", "mkdir", "move",
            "copy", "delete", "set_executable",
        }
        assert result == expected

    def test_fs_read_wildcard(self) -> None:
        result = resolve_allow_list(["fs.read.*"], TOOL_NAMES)
        expected = {
            "read", "list_dir", "glob", "grep", "stat", "diff",
        }
        assert result == expected

    def test_fs_write_wildcard(self) -> None:
        result = resolve_allow_list(["fs.write.*"], TOOL_NAMES)
        expected = {
            "write", "edit", "multi_edit", "mkdir", "move",
            "copy", "delete", "set_executable",
        }
        assert result == expected

    def test_io_wildcard(self) -> None:
        result = resolve_allow_list(["io.*"], TOOL_NAMES)
        expected = {"notepad", "http"}
        assert result == expected

    def test_git_wildcard(self) -> None:
        result = resolve_allow_list(["git.*"], TOOL_NAMES)
        # "git" has namespace "git", which matches prefix "git"
        assert result == {"git"}


class TestTagFilters:
    """Tag filters resolve to all tools with the specified tag."""

    def test_readonly_tag(self) -> None:
        result = resolve_allow_list(["#readonly"], TOOL_NAMES)
        expected = {
            "read", "list_dir", "glob", "grep", "stat", "diff",
            "git", "http", "consult",
        }
        assert result == expected

    def test_mutation_tag(self) -> None:
        result = resolve_allow_list(["#mutation"], TOOL_NAMES)
        expected = {
            "write", "edit", "multi_edit", "mkdir", "move",
            "copy", "delete", "set_executable",
            "git", "notepad", "http",
        }
        assert result == expected

    def test_nonexistent_tag(self) -> None:
        result = resolve_allow_list(["#nonexistent"], TOOL_NAMES)
        assert result == set()


class TestUniversalWildcard:
    """'*' resolves to all tools."""

    def test_star(self) -> None:
        result = resolve_allow_list(["*"], TOOL_NAMES)
        assert result == set(TOOL_NAMES.keys())


class TestMixed:
    """Combinations of explicit, wildcard, and tag entries."""

    def test_mixed(self) -> None:
        result = resolve_allow_list(
            ["git", "fs.read.*", "#mutation"],
            TOOL_NAMES,
        )
        expected = (
            {"git"}
            | {"read", "list_dir", "glob", "grep", "stat", "diff"}
            | {
                "write", "edit", "multi_edit", "mkdir", "move",
                "copy", "delete", "set_executable",
                "git", "notepad", "http",
            }
        )
        assert result == expected


class TestUnknownName:
    """Unknown explicit names are silently ignored."""

    def test_unknown_ignored(self) -> None:
        result = resolve_allow_list(
            ["read", "nonexistent_tool"], TOOL_NAMES,
        )
        assert result == {"read"}

    def test_all_unknown(self) -> None:
        result = resolve_allow_list(
            ["foo", "bar"], TOOL_NAMES,
        )
        assert result == set()


class TestEmptyInputs:
    """Edge cases with empty inputs."""

    def test_empty_allow_list(self) -> None:
        result = resolve_allow_list([], TOOL_NAMES)
        assert result == set()

    def test_empty_tool_names(self) -> None:
        result = resolve_allow_list(["read", "fs.*"], {})
        assert result == set()
