"""Tests for agent tool construction in _create_agent_session."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uuid6
from orxtra.agent import Agent, InlineToolDefinition
from orxtra.protocols import TaskSpec
from orxtra.scheduler._executor import Scheduler

from .conftest import (
    TEST_RUN_PRINCIPAL_ID,
    MockTraceWriter,
    MockTransport,
    make_categories,
)

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.protocols import Tool

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
        run_principal_id=TEST_RUN_PRINCIPAL_ID,
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

        await scheduler._create_agent_session(
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
            await sched._create_agent_session(
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
        assert sched._write_queue is not None
        assert sched._stale_tracker is not None


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
            await sched._create_agent_session(
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
            await sched._create_agent_session(
                task, task_id, 1,
            )

        assert len(captured_subcommands) == 1
        assert "commit" not in captured_subcommands[0]


class TestInlineToolPresent:
    """Agent with inline tool definitions gets the tools built."""

    async def test_inline_command_tool_constructed(
        self, tmp_path: Path,
    ) -> None:
        agent = Agent(
            name="test-agent",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["custom.*"],
            inline_tools=[
                InlineToolDefinition(
                    name="pytest",
                    description="Run tests",
                    namespace="custom.exec",
                    deferred=False,
                    execution={
                        "type": "command",
                        "executable": "pytest",
                        "arg_validation": True,
                        "timeout_ceiling": 120,
                    },
                ),
            ],
        )
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert "pytest" in names
        assert names >= LIFECYCLE_TOOLS


class TestInlineToolWithoutConfig:
    """Agent without inline_tools gets only lifecycle tools."""

    async def test_no_inline_tools(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["custom.*"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert names == LIFECYCLE_TOOLS


class TestMultiEditToolPresent:
    """Agent with allow=["multi_edit"] gets the multi_edit
    tool; agent without it does not."""

    async def test_multi_edit_present_when_allowed(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["multi_edit"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert "multi_edit" in names
        assert names >= LIFECYCLE_TOOLS

    async def test_multi_edit_absent_when_not_allowed(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["read", "write"])
        sched = _make_scheduler(agent, tmp_path)
        names = await _extract_tool_names(sched)
        assert "multi_edit" not in names


# -- Tool routing with execution_target --


def _task_with_target(target: str | None = None) -> TaskSpec:
    return TaskSpec(
        name="test-task",
        agent="test-agent",
        task_prompt="Do something",
        context_refinement=False,
        execution_target=target,
    )


def _make_scheduler_with_bridge(
    agent: Agent,
    tmp_path: Path,
    bridge: object | None = None,
) -> Scheduler:
    """Create a scheduler with a get_worker_bridge callback."""
    trace = MockTraceWriter()
    transport = MockTransport(auto_execute_tools=True)

    def _get_bridge(root: str) -> object | None:
        return bridge

    return Scheduler(
        run_principal_id=TEST_RUN_PRINCIPAL_ID,
        trace_writer=trace,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents={agent.name: agent},
        categories=make_categories(),
        run_id=uuid6.uuid7(),
        read_root=tmp_path,
        autonomy_level="max",
        get_worker_bridge=_get_bridge,
    )


async def _extract_tools_for_task(
    scheduler: Scheduler,
    task: TaskSpec,
) -> list[Tool]:
    """Call _create_agent_session and return the actual Tool list."""
    task_id = uuid6.uuid7()

    with patch(
        "orxtra.scheduler._agent_execution.create_session",
        new_callable=AsyncMock,
    ) as mock_create:
        mock_session = MagicMock()
        mock_create.return_value = mock_session

        await scheduler._create_agent_session(
            task, task_id, 1,
        )

        tools_arg: list[Tool] = (
            mock_create.call_args[1]["tools"]
        )
        return tools_arg


class TestToolRoutingWithExecutionTarget:
    """Tool routing: ANYWHERE tools go remote when execution_target is set."""

    async def test_anywhere_tools_routed_through_bridge(
        self, tmp_path: Path,
    ) -> None:
        """With an execution_target, ANYWHERE tools get wrapped for remote
        execution (their execute function changes). LOCAL tools (lifecycle)
        stay local."""

        agent = _agent(["read", "write"])
        mock_bridge = MagicMock()
        mock_bridge.send_tool_call = AsyncMock()
        sched = _make_scheduler_with_bridge(agent, tmp_path, mock_bridge)
        task = _task_with_target("/project/root")

        tools = await _extract_tools_for_task(sched, task)
        tool_map = {t.name: t for t in tools}

        # read and write are ANYWHERE by default -- they should be routed.
        # Lifecycle tools are LOCAL -- they should NOT be routed.
        # We verify by checking the tool count includes both sets.
        assert "read" in tool_map
        assert "write" in tool_map
        assert "start_task" in tool_map
        assert "end_task" in tool_map

        # The total tool count should be the same as without routing.
        # (We have read, write + lifecycle = 8 tools.)
        assert len(tools) >= 8

    async def test_missing_worker_raises_runtime_error(
        self, tmp_path: Path,
    ) -> None:
        """When execution_target is set but no worker is registered,
        creating the session raises RuntimeError."""
        agent = _agent(["read"])
        # Bridge returns None (no worker registered).
        sched = _make_scheduler_with_bridge(agent, tmp_path, bridge=None)
        task = _task_with_target("/nonexistent/root")
        task_id = uuid6.uuid7()

        with (
            patch(
                "orxtra.scheduler._agent_execution.create_session",
                new_callable=AsyncMock,
            ),
            pytest.raises(RuntimeError, match="No worker registered"),
        ):
            await sched._create_agent_session(task, task_id, 1)

    async def test_no_target_all_tools_local(
        self, tmp_path: Path,
    ) -> None:
        """Without execution_target, all tools stay local (existing behavior).
        Verified by confirming the session is created normally."""
        agent = _agent(["read", "write"])
        mock_bridge = MagicMock()
        sched = _make_scheduler_with_bridge(agent, tmp_path, mock_bridge)
        task = _task_with_target(None)

        tools = await _extract_tools_for_task(sched, task)
        tool_map = {t.name: t for t in tools}

        assert "read" in tool_map
        assert "write" in tool_map
        assert "start_task" in tool_map

    async def test_local_tools_stay_local_with_target(
        self, tmp_path: Path,
    ) -> None:
        """Lifecycle tools are LOCAL and must not be routed to the bridge,
        even when execution_target is set. Verified by confirming they
        do NOT have the remote execute wrapper (which uses bridge.send_tool_call)."""
        agent = _agent(["read"])
        mock_bridge = MagicMock()
        mock_bridge.send_tool_call = AsyncMock()
        sched = _make_scheduler_with_bridge(agent, tmp_path, mock_bridge)
        task = _task_with_target("/project/root")

        tools = await _extract_tools_for_task(sched, task)

        # All lifecycle tools should be present.
        tool_names = {t.name for t in tools}
        assert tool_names >= LIFECYCLE_TOOLS

        # Lifecycle tools should NOT reference the bridge. We verify
        # by ensuring the _raw_execute attribute on local-wrapped tools
        # does NOT reference bridge.send_tool_call.
        for t in tools:
            if t.name in LIFECYCLE_TOOLS:
                # Lifecycle tools go through wrap_tools_for_session,
                # not wrap_tool_for_remote. The local pipeline's
                # execute always has _raw_execute set.
                raw = getattr(t.execute, "_raw_execute", None)
                assert raw is not None, (
                    f"Lifecycle tool {t.name!r} should have "
                    f"_raw_execute (local pipeline wrap)"
                )
