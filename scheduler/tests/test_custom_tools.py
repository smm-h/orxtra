"""Tests for custom_tools injection on Scheduler."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.protocols._task import TaskSpec
from orxtra.protocols._tool import Tool
from orxtra.scheduler._executor import Scheduler

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_categories,
)

if TYPE_CHECKING:
    from pathlib import Path


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


def _make_custom_tool(name: str) -> Tool:
    """Create a minimal Tool instance for testing."""
    return Tool(
        name=name,
        description=f"Custom tool: {name}",
        parameters={"type": "object", "properties": {}},
        execute=AsyncMock(return_value="ok"),
    )


def _make_scheduler(
    agent: Agent,
    tmp_path: Path,
    *,
    custom_tools: dict[str, object] | None = None,
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
        custom_tools=custom_tools,  # type: ignore[arg-type]
    )


async def _extract_tool_names(
    scheduler: Scheduler,
) -> set[str]:
    """Call _create_agent_session with create_session
    mocked, and return the set of tool names passed."""
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


class TestCustomToolInAllowList:
    """Custom tool appears in agent's session when
    in the allow list."""

    async def test_custom_tool_present(
        self, tmp_path: Path,
    ) -> None:
        custom_tools = {
            "my_custom": lambda: _make_custom_tool(
                "my_custom",
            ),
        }
        agent = _agent(["my_custom"])
        sched = _make_scheduler(
            agent, tmp_path, custom_tools=custom_tools,
        )
        names = await _extract_tool_names(sched)
        assert "my_custom" in names


class TestCustomToolNotInAllowList:
    """Custom tool NOT included when not in allow list."""

    async def test_custom_tool_absent(
        self, tmp_path: Path,
    ) -> None:
        custom_tools = {
            "my_custom": lambda: _make_custom_tool(
                "my_custom",
            ),
        }
        agent = _agent(["read"])
        sched = _make_scheduler(
            agent, tmp_path, custom_tools=custom_tools,
        )
        names = await _extract_tool_names(sched)
        assert "my_custom" not in names
        assert "read" in names


class TestNameCollisionBuiltinWins:
    """Name collision with built-in resolves to built-in."""

    async def test_builtin_wins(
        self, tmp_path: Path,
    ) -> None:
        # "read" is a built-in tool name. The custom tool
        # factory should NOT be called.
        factory = MagicMock(
            return_value=_make_custom_tool("read"),
        )
        custom_tools = {"read": factory}
        agent = _agent(["read"])
        sched = _make_scheduler(
            agent, tmp_path, custom_tools=custom_tools,
        )
        names = await _extract_tool_names(sched)
        assert "read" in names
        # The custom factory must not have been called
        factory.assert_not_called()


class TestEmptyCustomTools:
    """Empty custom_tools dict works without errors."""

    async def test_empty_dict(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["read"])
        sched = _make_scheduler(
            agent, tmp_path, custom_tools={},
        )
        names = await _extract_tool_names(sched)
        assert "read" in names


class TestNoneCustomTools:
    """None custom_tools (default) works without errors."""

    async def test_none_default(
        self, tmp_path: Path,
    ) -> None:
        agent = _agent(["read"])
        sched = _make_scheduler(
            agent, tmp_path, custom_tools=None,
        )
        names = await _extract_tool_names(sched)
        assert "read" in names
