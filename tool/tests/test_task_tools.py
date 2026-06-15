"""Tests for task lifecycle tool constructors."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from orxt.protocols._tool import ToolError
from orxt.tool._task_tools import (
    make_create_task_tool,
    make_create_wait_for_tool,
    make_create_workflow_tool,
    make_end_task_tool,
    make_start_task_tool,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SESSION_ID = "session-42"


def _mock_scheduler(return_value: str = "ok") -> MagicMock:
    mock = MagicMock()
    mock.handle_start_task = AsyncMock(return_value=return_value)
    mock.handle_end_task = AsyncMock(return_value=return_value)
    mock.handle_create_task = AsyncMock(return_value=return_value)
    mock.handle_create_workflow = AsyncMock(return_value=return_value)
    mock.handle_create_wait_for = AsyncMock(return_value=return_value)
    return mock


# ---------------------------------------------------------------------------
# TestStartTaskTool
# ---------------------------------------------------------------------------


class TestStartTaskTool:
    """Tests for the start_task tool constructor."""

    @pytest.mark.asyncio
    async def test_delegates_to_scheduler(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_start_task_tool(scheduler, _SESSION_ID)
        await tool.execute({"task_id": "t1"})
        scheduler.handle_start_task.assert_awaited_once_with(_SESSION_ID, "t1")

    @pytest.mark.asyncio
    async def test_returns_scheduler_response(self) -> None:
        scheduler = _mock_scheduler(return_value="Task started")
        tool = make_start_task_tool(scheduler, _SESSION_ID)
        result = await tool.execute({"task_id": "t1"})
        assert result == "Task started"

    @pytest.mark.asyncio
    async def test_missing_task_id_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_start_task_tool(scheduler, _SESSION_ID)
        with pytest.raises(ToolError):
            await tool.execute({})

    def test_tool_name_is_start_task(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_start_task_tool(scheduler, _SESSION_ID)
        assert tool.name == "start_task"


# ---------------------------------------------------------------------------
# TestEndTaskTool
# ---------------------------------------------------------------------------


class TestEndTaskTool:
    """Tests for the end_task tool constructor."""

    @pytest.mark.asyncio
    async def test_delegates_to_scheduler(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_end_task_tool(scheduler, _SESSION_ID)
        await tool.execute({"message": "Done"})
        scheduler.handle_end_task.assert_awaited_once_with(_SESSION_ID, "Done")

    @pytest.mark.asyncio
    async def test_returns_scheduler_response(self) -> None:
        scheduler = _mock_scheduler(return_value="Task ended")
        tool = make_end_task_tool(scheduler, _SESSION_ID)
        result = await tool.execute({"message": "Done"})
        assert result == "Task ended"

    @pytest.mark.asyncio
    async def test_missing_message_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_end_task_tool(scheduler, _SESSION_ID)
        with pytest.raises(ToolError):
            await tool.execute({})

    def test_tool_name_is_end_task(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_end_task_tool(scheduler, _SESSION_ID)
        assert tool.name == "end_task"


# ---------------------------------------------------------------------------
# TestCreateTaskTool
# ---------------------------------------------------------------------------


class TestCreateTaskTool:
    """Tests for the create_task tool constructor."""

    @pytest.mark.asyncio
    async def test_required_params_delegate_correctly(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_task_tool(scheduler, _SESSION_ID)
        args = {
            "name": "subtask-1",
            "agent": "coder",
            "task_prompt": "Fix the bug",
            "timeout": 300,
            "context_refinement": True,
        }
        await tool.execute(args)
        scheduler.handle_create_task.assert_awaited_once_with(_SESSION_ID, args)

    @pytest.mark.asyncio
    async def test_optional_params_included(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_task_tool(scheduler, _SESSION_ID)
        args = {
            "name": "subtask-2",
            "agent": "reviewer",
            "task_prompt": "Review the PR",
            "timeout": 120,
            "context_refinement": False,
            "budget": 0.50,
            "write_paths": ["/src/main.py"],
            "depends_on": ["task-a", "task-b"],
        }
        await tool.execute(args)
        scheduler.handle_create_task.assert_awaited_once_with(_SESSION_ID, args)

    @pytest.mark.asyncio
    async def test_missing_required_param_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_task_tool(scheduler, _SESSION_ID)
        args = {
            "name": "subtask-3",
            # "agent" omitted
            "task_prompt": "Do something",
            "timeout": 60,
            "context_refinement": False,
        }
        with pytest.raises(ToolError):
            await tool.execute(args)

    @pytest.mark.asyncio
    async def test_returns_task_id(self) -> None:
        scheduler = _mock_scheduler(return_value="task-abc")
        tool = make_create_task_tool(scheduler, _SESSION_ID)
        args = {
            "name": "subtask-4",
            "agent": "coder",
            "task_prompt": "Implement feature",
            "timeout": 600,
            "context_refinement": True,
        }
        result = await tool.execute(args)
        assert result == "task-abc"

    def test_tool_name_is_create_task(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_task_tool(scheduler, _SESSION_ID)
        assert tool.name == "create_task"

    @pytest.mark.asyncio
    async def test_extra_field_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_task_tool(scheduler, _SESSION_ID)
        args = {
            "name": "subtask-5",
            "agent": "coder",
            "task_prompt": "Do work",
            "timeout": 60,
            "context_refinement": False,
            "bogus_field": "should not be allowed",
        }
        with pytest.raises(ToolError):
            await tool.execute(args)


# ---------------------------------------------------------------------------
# TestCreateWorkflowTool
# ---------------------------------------------------------------------------


class TestCreateWorkflowTool:
    """Tests for the create_workflow tool constructor."""

    @pytest.mark.asyncio
    async def test_valid_params_delegate(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_workflow_tool(scheduler, _SESSION_ID)
        args = {
            "name": "deploy-flow",
            "description": "Deploy to production",
            "goals": ["a", "b"],
        }
        await tool.execute(args)
        scheduler.handle_create_workflow.assert_awaited_once_with(
            _SESSION_ID, args
        )

    @pytest.mark.asyncio
    async def test_empty_goals_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_workflow_tool(scheduler, _SESSION_ID)
        args = {
            "name": "empty-goals",
            "description": "Should fail",
            "goals": [],
        }
        with pytest.raises(ToolError):
            await tool.execute(args)

    @pytest.mark.asyncio
    async def test_missing_goals_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_workflow_tool(scheduler, _SESSION_ID)
        args = {
            "name": "no-goals",
            "description": "Should also fail",
        }
        with pytest.raises(ToolError):
            await tool.execute(args)

    @pytest.mark.asyncio
    async def test_returns_workflow_id(self) -> None:
        scheduler = _mock_scheduler(return_value="wf-1")
        tool = make_create_workflow_tool(scheduler, _SESSION_ID)
        args = {
            "name": "test-wf",
            "description": "A test workflow",
            "goals": ["goal-1"],
        }
        result = await tool.execute(args)
        assert result == "wf-1"

    def test_tool_name_is_create_workflow(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_workflow_tool(scheduler, _SESSION_ID)
        assert tool.name == "create_workflow"


# ---------------------------------------------------------------------------
# TestCreateWaitForTool
# ---------------------------------------------------------------------------


class TestCreateWaitForTool:
    """Tests for the create_wait_for tool constructor."""

    @pytest.mark.asyncio
    async def test_valid_params_delegate(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_wait_for_tool(scheduler, _SESSION_ID)
        args = {
            "name": "wait-deploy",
            "event_name": "deploy_complete",
            "timeout": 600,
        }
        await tool.execute(args)
        scheduler.handle_create_wait_for.assert_awaited_once_with(
            _SESSION_ID, args
        )

    @pytest.mark.asyncio
    async def test_missing_event_name_raises_tool_error(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_wait_for_tool(scheduler, _SESSION_ID)
        args = {
            "name": "wait-missing",
            "timeout": 60,
        }
        with pytest.raises(ToolError):
            await tool.execute(args)

    @pytest.mark.asyncio
    async def test_optional_depends_on_passed_through(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_wait_for_tool(scheduler, _SESSION_ID)
        args = {
            "name": "wait-deps",
            "event_name": "build_done",
            "timeout": 120,
            "depends_on": ["task-x", "task-y"],
        }
        await tool.execute(args)
        scheduler.handle_create_wait_for.assert_awaited_once_with(
            _SESSION_ID, args
        )

    @pytest.mark.asyncio
    async def test_returns_task_id(self) -> None:
        scheduler = _mock_scheduler(return_value="task-w1")
        tool = make_create_wait_for_tool(scheduler, _SESSION_ID)
        args = {
            "name": "wait-ret",
            "event_name": "signal",
            "timeout": 30,
        }
        result = await tool.execute(args)
        assert result == "task-w1"

    def test_tool_name_is_create_wait_for(self) -> None:
        scheduler = _mock_scheduler()
        tool = make_create_wait_for_tool(scheduler, _SESSION_ID)
        assert tool.name == "create_wait_for"
