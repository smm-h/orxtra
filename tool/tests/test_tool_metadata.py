"""Verification tests for namespace and tags on all 26 built-in tools.

Each make_* factory is called with minimal dependencies and the resulting
Tool is checked for the correct namespace and tags.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
from orxtra.protocols import Tool
from orxtra.tool._consult_tool import make_consult_tool
from orxtra.tool._exec_tool import make_exec_tool
from orxtra.tool._git_tool import make_git_tool
from orxtra.tool._http_tool import make_http_tool
from orxtra.tool._notepad_tool import make_notepad_tool
from orxtra.tool._read_tools import (
    make_diff_tool,
    make_glob_tool,
    make_grep_tool,
    make_list_dir_tool,
    make_read_tool,
    make_stat_tool,
)
from orxtra.tool._shell_tool import make_shell_tool
from orxtra.tool._task_tools import (
    make_await_task_tool,
    make_create_task_tool,
    make_create_wait_for_tool,
    make_create_workflow_tool,
    make_end_task_tool,
    make_start_task_tool,
)
from orxtra.tool._write_tools import (
    make_copy_tool,
    make_delete_tool,
    make_edit_tool,
    make_mkdir_tool,
    make_move_tool,
    make_multi_edit_tool,
    make_set_executable_tool,
    make_write_tool,
)
from orxtra.write_safety import StaleWriteTracker, WriteQueue


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_ROOT = Path("/tmp/test_root")


@pytest.fixture
def write_deps() -> dict[str, Any]:
    """Common dependencies for write tools."""
    return {
        "read_root": _ROOT,
        "write_scope": None,
        "queue": WriteQueue(),
        "tracker": StaleWriteTracker(),
        "session_id": "test",
    }


@pytest.fixture
def scheduler_ref() -> Any:
    """Minimal scheduler ref for task tools."""
    mock = AsyncMock()
    mock.handle_start_task = AsyncMock(return_value="ok")
    mock.handle_end_task = AsyncMock(return_value="ok")
    mock.handle_create_task = AsyncMock(return_value="ok")
    mock.handle_create_workflow = AsyncMock(return_value="ok")
    mock.handle_create_wait_for = AsyncMock(return_value="ok")
    mock.handle_await_task = AsyncMock(return_value="ok")
    return mock


# ---------------------------------------------------------------------------
# Expected metadata for all tools
# ---------------------------------------------------------------------------

# (tool_name, expected_namespace, expected_tags)
EXPECTED_METADATA: list[tuple[str, str, frozenset[str]]] = [
    # fs.read tools
    ("read", "fs.read", frozenset({"readonly"})),
    ("list_dir", "fs.read", frozenset({"readonly"})),
    ("glob", "fs.read", frozenset({"readonly"})),
    ("grep", "fs.read", frozenset({"readonly"})),
    ("stat", "fs.read", frozenset({"readonly"})),
    ("diff", "fs.read", frozenset({"readonly"})),
    # fs.write tools
    ("write", "fs.write", frozenset({"mutation"})),
    ("edit", "fs.write", frozenset({"mutation"})),
    ("multi_edit", "fs.write", frozenset({"mutation"})),
    ("mkdir", "fs.write", frozenset({"mutation"})),
    ("move", "fs.write", frozenset({"mutation"})),
    ("copy", "fs.write", frozenset({"mutation"})),
    ("delete", "fs.write", frozenset({"mutation"})),
    ("set_executable", "fs.write", frozenset({"mutation"})),
    # git
    ("git", "git", frozenset({"readonly", "mutation"})),
    # task.lifecycle
    ("start_task", "task.lifecycle", frozenset({"lifecycle"})),
    ("end_task", "task.lifecycle", frozenset({"lifecycle"})),
    ("create_task", "task.lifecycle", frozenset({"lifecycle"})),
    ("create_workflow", "task.lifecycle", frozenset({"lifecycle"})),
    ("create_wait_for", "task.lifecycle", frozenset({"lifecycle"})),
    ("await_task", "task.lifecycle", frozenset({"lifecycle", "suspending"})),
    # io.http (full mode)
    ("http_full", "io.http", frozenset({"readonly", "mutation"})),
    # io.http (consult mode)
    ("http_consult", "io.http", frozenset({"readonly"})),
    # io.notepad
    ("notepad", "io.notepad", frozenset({"mutation"})),
    # exec
    ("shell", "exec", frozenset({"mutation"})),
    ("exec", "exec", frozenset({"mutation"})),
    # meta.consult
    ("consult", "meta.consult", frozenset({"readonly"})),
]


def _build_all_tools(
    write_deps: dict[str, Any],
    scheduler_ref: Any,
) -> dict[str, Tool]:
    """Build one instance of every built-in tool."""
    tools: dict[str, Tool] = {}

    # fs.read
    tools["read"] = make_read_tool(
        read_root=_ROOT, preview_threshold=50000, preview_lines=50,
    )
    tools["list_dir"] = make_list_dir_tool(read_root=_ROOT)
    tools["glob"] = make_glob_tool(read_root=_ROOT)
    tools["grep"] = make_grep_tool(
        read_root=_ROOT, preview_threshold=50000, preview_lines=50,
    )
    tools["stat"] = make_stat_tool(read_root=_ROOT)
    tools["diff"] = make_diff_tool(read_root=_ROOT)

    # fs.write
    tools["write"] = make_write_tool(**write_deps)
    tools["edit"] = make_edit_tool(**write_deps)
    tools["multi_edit"] = make_multi_edit_tool(**write_deps)
    tools["mkdir"] = make_mkdir_tool(
        read_root=_ROOT, write_scope=None,
    )
    tools["move"] = make_move_tool(**write_deps)
    tools["copy"] = make_copy_tool(**write_deps)
    tools["delete"] = make_delete_tool(read_root=_ROOT, write_scope=None)
    tools["set_executable"] = make_set_executable_tool(
        read_root=_ROOT, write_scope=None,
    )

    # git
    tools["git"] = make_git_tool(
        read_root=_ROOT,
        allowed_subcommands=["status", "log", "commit"],
    )

    # task.lifecycle
    tools["start_task"] = make_start_task_tool(
        scheduler_ref=scheduler_ref, session_id="test",
    )
    tools["end_task"] = make_end_task_tool(
        scheduler_ref=scheduler_ref, session_id="test",
    )
    tools["create_task"] = make_create_task_tool(
        scheduler_ref=scheduler_ref, session_id="test",
    )
    tools["create_workflow"] = make_create_workflow_tool(
        scheduler_ref=scheduler_ref, session_id="test",
    )
    tools["create_wait_for"] = make_create_wait_for_tool(
        scheduler_ref=scheduler_ref, session_id="test",
    )
    tools["await_task"] = make_await_task_tool(
        scheduler_ref=scheduler_ref, session_id="test",
    )

    # io.http
    tools["http_full"] = make_http_tool(
        allowed_hosts="allow_all", consult_mode=False,
    )
    tools["http_consult"] = make_http_tool(
        allowed_hosts="allow_all", consult_mode=True,
    )

    # io.notepad
    tools["notepad"] = make_notepad_tool(
        trace_writer=AsyncMock(),
        run_id="run-1",
        task_name="task-1",
        agent_name="agent-1",
    )

    # exec
    tools["shell"] = make_shell_tool(
        allowed_binaries=["echo"],
        description="Test shell",
        read_root=_ROOT,
        timeout_ceiling=30,
        preview_threshold=50000,
        preview_lines=50,
    )
    tools["exec"] = make_exec_tool(
        executable="echo",
        description="Test exec",
        read_root=_ROOT,
        timeout_ceiling=30,
        preview_threshold=50000,
        preview_lines=50,
    )

    # meta.consult
    tools["consult"] = make_consult_tool(
        tool_registry={},
        transport_registry={},
        trace_writer=None,
        run_id=None,
        read_root=_ROOT,
        categories={},
        agents={},
    )

    return tools


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolMetadata:
    """Verify namespace and tags on all 26 built-in tools."""

    @pytest.fixture(autouse=True)
    def _setup(
        self,
        write_deps: dict[str, Any],
        scheduler_ref: Any,
    ) -> None:
        self._tools = _build_all_tools(write_deps, scheduler_ref)

    @pytest.mark.parametrize(
        ("tool_key", "expected_ns", "expected_tags"),
        EXPECTED_METADATA,
        ids=[t[0] for t in EXPECTED_METADATA],
    )
    def test_namespace(
        self,
        tool_key: str,
        expected_ns: str,
        expected_tags: frozenset[str],
    ) -> None:
        """Each tool has the correct namespace."""
        t = self._tools[tool_key]
        assert t.namespace == expected_ns, (
            f"Tool {tool_key!r}: expected namespace={expected_ns!r}, "
            f"got {t.namespace!r}"
        )

    @pytest.mark.parametrize(
        ("tool_key", "expected_ns", "expected_tags"),
        EXPECTED_METADATA,
        ids=[t[0] for t in EXPECTED_METADATA],
    )
    def test_tags(
        self,
        tool_key: str,
        expected_ns: str,
        expected_tags: frozenset[str],
    ) -> None:
        """Each tool has the correct tags."""
        t = self._tools[tool_key]
        assert t.tags == expected_tags, (
            f"Tool {tool_key!r}: expected tags={expected_tags!r}, "
            f"got {t.tags!r}"
        )

    def test_exec_tool_name_is_executable(self) -> None:
        """make_exec_tool uses the executable name as the tool name."""
        t = self._tools["exec"]
        assert t.name == "echo"

    def test_exec_tool_namespace_from_template(self) -> None:
        """make_exec_tool preserves the template namespace."""
        t = self._tools["exec"]
        assert t.namespace == "exec"

    def test_all_expected_tools_covered(self) -> None:
        """Every tool in EXPECTED_METADATA has a corresponding built tool."""
        expected_keys = {t[0] for t in EXPECTED_METADATA}
        built_keys = set(self._tools.keys())
        missing = expected_keys - built_keys
        assert not missing, f"Missing tools in build: {missing}"

    def test_total_tool_count(self) -> None:
        """We build 28 tool instances (26 unique tools + 2 http modes)."""
        # 6 read + 8 write + 1 git + 6 task + 2 http + 1 notepad
        # + 1 shell + 1 exec + 1 consult = 27 instances
        assert len(self._tools) == 27
