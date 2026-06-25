from __future__ import annotations

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest

from orxtra.protocols import ActionExecutor, EventAction, LogAction, ScriptAction, WorkflowAction
from orxtra.services._actions import (
    ServicesActionExecutor,
    execute_service_action,
)


@pytest.fixture
def mock_pool() -> AsyncMock:
    return AsyncMock()


# -- ServicesActionExecutor --


async def test_satisfies_protocol(mock_pool: AsyncMock) -> None:
    executor = ServicesActionExecutor(mock_pool)
    assert isinstance(executor, ActionExecutor)


@patch("orxtra.services._run.start_run_from_file", new_callable=AsyncMock)
async def test_execute_workflow_calls_start_run(
    mock_start: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_start.return_value = uuid4()
    executor = ServicesActionExecutor(mock_pool, intent_prefix="test")

    await executor.execute_workflow(
        "/path/to/workflow.toml",
        {"key": "value"},
        [{"event_type": "task_completed"}],
    )

    mock_start.assert_awaited_once()
    call_args = mock_start.call_args
    assert call_args[0][0] is mock_pool
    assert "test:" in call_args[0][1]
    assert "workflow" in call_args[0][1]


@patch("orxtra.services._run.start_run_from_file", new_callable=AsyncMock)
async def test_execute_workflow_intent_includes_event_count(
    mock_start: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_start.return_value = uuid4()
    executor = ServicesActionExecutor(mock_pool)

    events = [{"type": "a"}, {"type": "b"}, {"type": "c"}]
    await executor.execute_workflow("/w.toml", {}, events)

    intent = mock_start.call_args[0][1]
    assert "3 events" in intent


# -- execute_service_action --


async def test_execute_service_action_log() -> None:
    action = LogAction(message="test log", level="info")
    # LogAction does not need pool -- executes directly.
    await execute_service_action(action, [{"data": "x"}])


@patch("orxtra.services._run.start_run_from_file", new_callable=AsyncMock)
async def test_execute_service_action_workflow(
    mock_start: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_start.return_value = uuid4()
    action = WorkflowAction(workflow_path="/test.toml", config={})

    await execute_service_action(
        action, [{"event_type": "x"}], pool=mock_pool,
    )

    mock_start.assert_awaited_once()


@patch("orxtra.services._events.TraceWriter")
async def test_execute_service_action_event(
    mock_writer_cls: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=uuid4())
    action = EventAction(event_type="custom_event", data={"key": "val"})

    await execute_service_action(
        action, [], pool=mock_pool,
    )

    mock_writer.write_event.assert_awaited_once()


async def test_execute_service_action_workflow_without_pool() -> None:
    action = WorkflowAction(workflow_path="/test.toml", config={})
    with pytest.raises(RuntimeError, match="ActionExecutor"):
        await execute_service_action(action, [{"data": "x"}])


async def test_execute_service_action_event_without_pool() -> None:
    action = EventAction(event_type="test", data={})
    with pytest.raises(RuntimeError, match="event_fire_callback"):
        await execute_service_action(action, [])
