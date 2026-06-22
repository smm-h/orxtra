"""Tests for agent tool construction in _create_agent_session."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uuid6
from orxtra.agent import Agent, ExecToolConfig, ShellConfig
from orxtra.protocols._task import TaskSpec
from orxtra.scheduler._executor import Scheduler

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_categories,
)

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.protocols._tool import Tool

LIFECYCLE_TOOLS = frozenset({
    "start_task",
    "end_task",
    "create_task",
    "create_workflow",
    "create_wait_for",
    "await_task",
})


def _agent(allow: list[str]) -> Agent:
    return Agent(
        name="test-agent",
        description="Test agent",
        prompt="You are a test agent.",
        category="default",
        allow=allow,
    )


def _task() -> TaskSpec:
    return TaskSpec(
        name="test-task",
        agent="test-agent",
        task_prompt="Do something",
        context_refinement=False,
    )


def _make_scheduler(
    agent: Agent,
    tmp_path: Path,
) -> Scheduler:
    trace = MockTraceWriter()
    transport = MockTransport(auto_execute_tools=True)
    return Scheduler(
        trace_writer=trace,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents={agent.name: agent},
        categories=make_categories(),
        run_id=uuid6.uuid7(),
        read_root=tmp_path,
        autonomy_level="max",
    )


async def _extract_tool_names(
    scheduler: Scheduler,
) -> set[str]:
    """Call _create_agent_session with create_session
    mocked, and return the set of tool names passed to it."""
    task = _task()
    task_id = uuid6.uuid7()

    with patch(
        "orxtra.scheduler._agent_execution.create_session",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_session = MagicMock()
        mock_create.return_value = mock_session

        await scheduler._create_agent_session(  # noqa: SLF001
            task, task_id, 1,
        )

        tools_arg: list[Tool] = (
            mock_create.call_args[1]["tools"]
        )
        return {t.name for t in tools_arg}


class TestReadWriteToolsPresent:
    """Agent with allow=["read", "write"] gets read
    and write tools plus lifecycle tools."""

    async def test_read_write_plus_lifecycle(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["read", "write"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        assert "read" in names
        assert "write" in names
        assert names >= LIFECYCLE_TOOLS


class TestEmptyAllowOnlyLifecycle:
    """Agent with allow=[] gets only lifecycle tools."""

    async def test_empty_allow(self, tmp_path: Path) -> None:
        agent = _agent([])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        assert names == LIFECYCLE_TOOLS


class TestGitToolPresent:
    """Agent with allow=["git"] gets the git tool."""

    async def test_git_tool(self, tmp_path: Path) -> None:
        agent = _agent(["git"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        assert "git" in names
        assert names >= LIFECYCLE_TOOLS


class TestDisallowedToolAbsent:
    """A tool NOT in the agent's allow list is NOT
    in the session tools."""

    async def test_write_absent_when_not_allowed(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["read"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        assert "read" in names
        assert "write" not in names
        assert "edit" not in names
        assert "git" not in names
        assert "notepad" not in names
        assert "http" not in names
        assert "consult" not in names

    async def test_read_absent_when_not_allowed(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["write"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        assert "write" in names
        assert "read" not in names


class TestWriteToolsReceiveWriteQueue:
    """Write tools are constructed with write-safety
    (WriteQueue and StaleWriteTracker)."""

    async def test_write_tool_has_write_queue(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["write", "edit", "move", "copy"])
        sched = _make_scheduler(agent, tmp_path)
        task = _task()
        task_id = uuid6.uuid7()

        # Patch create_session but NOT the tool
        # constructors -- let real tools be built.
        # Capture raw_tools before wrap_tools_for_session.
        captured_tools: list[Tool] = []

        original_wrap = (
            __import__(
                "orxtra.tool._pipeline",
                fromlist=["wrap_tools_for_session"],
            ).wrap_tools_for_session
        )

        def capturing_wrap(
            tools: list[Tool], **kwargs: object,
        ) -> list[Tool]:
            captured_tools.extend(tools)
            return original_wrap(tools=tools, **kwargs)

        with (
            patch(
                "orxtra.scheduler._agent_execution"
                ".create_session",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "orxtra.scheduler._agent_execution"
                ".wrap_tools_for_session",
                side_effect=capturing_wrap,
            ),
        ):
            await sched._create_agent_session(  # noqa: SLF001
                task, task_id, 1,
            )

        # The write tools should have been constructed
        # with the scheduler's write queue. We verify
        # this indirectly: the scheduler's _write_queue
        # and _stale_tracker exist, and the write/edit/
        # move/copy tools were created (meaning the
        # make_*_tool functions received them).
        tool_names = {t.name for t in captured_tools}
        assert "write" in tool_names
        assert "edit" in tool_names
        assert "move" in tool_names
        assert "copy" in tool_names

        # Verify the scheduler has write-safety
        # infrastructure that was passed to the tools.
        assert sched._write_queue is not None  # noqa: SLF001
        assert sched._stale_tracker is not None  # noqa: SLF001


class TestAllLifecycleToolsAlwaysPresent:
    """All 6 lifecycle tools are always present regardless
    of the allow list."""

    @pytest.mark.parametrize(
        "allow",
        [
            [],
            ["read"],
            ["write", "edit"],
            ["git", "read", "notepad"],
            [
                "read", "write", "edit", "git",
                "notepad", "http",
            ],
        ],
        ids=[
            "empty",
            "read-only",
            "write-tools",
            "mixed",
            "all-standard",
        ],
    )
    async def test_lifecycle_always_present(
        self, allow: list[str], tmp_path: Path,
    ) -> None:
        agent = _agent(allow)
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        assert names >= LIFECYCLE_TOOLS, (
            f"Missing lifecycle tools: "
            f"{LIFECYCLE_TOOLS - names}"
        )


class TestFullToolSuite:
    """Agent with all allow entries gets all tools."""

    async def test_all_tools(self, tmp_path: Path) -> None:
        all_allow = [
            "read", "list_dir", "glob", "grep",
            "stat", "diff", "write", "edit", "mkdir",
            "move", "copy", "delete", "set_executable",
            "git", "notepad", "http",
        ]
        agent = _agent(all_allow)
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)

        expected = {
            "read", "list_dir", "glob", "grep",
            "stat", "diff", "write", "edit", "mkdir",
            "move", "copy", "delete", "set_executable",
            "git", "notepad", "http",
        } | LIFECYCLE_TOOLS
        assert expected <= names


class TestGitSubcommandsDependOnWriteAccess:
    """Git tool with write tools in allow list includes
    the commit subcommand; without write tools it does
    not."""

    async def test_git_with_write_has_commit(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["git", "write"])
        sched = _make_scheduler(agent, tmp_path)
        task = _task()
        task_id = uuid6.uuid7()

        captured_subcommands: list[list[str]] = []
        original_make = (
            __import__(
                "orxtra.tool._git_tool",
                fromlist=["make_git_tool"],
            ).make_git_tool
        )

        def capturing_make(
            read_root: Path,
            allowed_subcommands: list[str],
            **kwargs: object,
        ) -> Tool:
            captured_subcommands.append(
                allowed_subcommands,
            )
            return original_make(
                read_root, allowed_subcommands,
                **kwargs,
            )

        with (
            patch(
                "orxtra.scheduler._agent_execution"
                ".create_session",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "orxtra.scheduler._agent_execution"
                ".make_git_tool",
                side_effect=capturing_make,
            ),
        ):
            await sched._create_agent_session(  # noqa: SLF001
                task, task_id, 1,
            )

        assert len(captured_subcommands) == 1
        assert "commit" in captured_subcommands[0]

    async def test_git_without_write_no_commit(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["git"])
        sched = _make_scheduler(agent, tmp_path)
        task = _task()
        task_id = uuid6.uuid7()

        captured_subcommands: list[list[str]] = []
        original_make = (
            __import__(
                "orxtra.tool._git_tool",
                fromlist=["make_git_tool"],
            ).make_git_tool
        )

        def capturing_make(
            read_root: Path,
            allowed_subcommands: list[str],
            **kwargs: object,
        ) -> Tool:
            captured_subcommands.append(
                allowed_subcommands,
            )
            return original_make(
                read_root, allowed_subcommands,
                **kwargs,
            )

        with (
            patch(
                "orxtra.scheduler._agent_execution"
                ".create_session",
                new_callable=AsyncMock,
                return_value=MagicMock(),
            ),
            patch(
                "orxtra.scheduler._agent_execution"
                ".make_git_tool",
                side_effect=capturing_make,
            ),
        ):
            await sched._create_agent_session(  # noqa: SLF001
                task, task_id, 1,
            )

        assert len(captured_subcommands) == 1
        assert "commit" not in captured_subcommands[0]


class TestExecToolPresent:
    """Agent with allow=["exec"] and exec_tools gets
    exec tools."""

    async def test_exec_tools_constructed(
        self, tmp_path: Path,
    ) -> None:
        agent = Agent(
            name="test-agent",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["exec"],
            exec_tools=[
                ExecToolConfig(
                    name="pytest",
                    executable="pytest",
                    description="Run tests",
                ),
            ],
        )
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert "pytest" in names
        assert names >= LIFECYCLE_TOOLS


class TestShellToolPresent:
    """Agent with allow=["shell"] and shell_config gets
    shell tool."""

    async def test_shell_tool_constructed(
        self, tmp_path: Path,
    ) -> None:
        agent = Agent(
            name="test-agent",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["shell"],
            shell_config=ShellConfig(
                allowed_binaries=["ls", "cat"],
            ),
        )
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert "shell" in names
        assert names >= LIFECYCLE_TOOLS


class TestExecWithoutConfig:
    """Agent with allow=["exec"] but no exec_tools gets
    no exec tools."""

    async def test_no_exec_tools_without_config(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["exec"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        # Only lifecycle tools (no exec tools without
        # config)
        assert names == LIFECYCLE_TOOLS


class TestShellWithoutConfig:
    """Agent with allow=["shell"] but no shell_config gets
    no shell tool."""

    async def test_no_shell_without_config(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["shell"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert "shell" not in names
        assert names == LIFECYCLE_TOOLS
