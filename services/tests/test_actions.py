from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from orxtra.protocols import (
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    ActionExecutor,
    EventAction,
    LogAction,
    Principal,
    WorkflowAction,
)
from orxtra.services._actions import (
    ServicesActionExecutor,
    execute_service_action,
)


@pytest.fixture
def mock_pool() -> AsyncMock:
    return AsyncMock()


def _system_principal() -> Principal:
    return Principal(
        id=uuid4(),
        kind=KIND_SYSTEM,
        external_ref=SYSTEM_PRINCIPAL_EXTERNAL_REF,
        display_name="system",
        created_at=datetime.now(UTC),
    )


def _patch_storage(mock_storage_cls: AsyncMock) -> Principal:
    """Wire a mocked PgPrincipalStorage that resolves the system principal.

    execute_workflow builds a PgPrincipalStorage from its pool and resolves the
    system principal as the dispatch-triggered run's creator; the tests mock
    that storage so no real pool is touched.
    """
    system = _system_principal()
    storage = mock_storage_cls.return_value
    storage.get_principal_by_ref = AsyncMock(return_value=system)
    return system


# -- ServicesActionExecutor --


async def test_satisfies_protocol(mock_pool: AsyncMock) -> None:
    executor = ServicesActionExecutor(mock_pool)
    assert isinstance(executor, ActionExecutor)


@patch("orxtra.identity.PgPrincipalStorage")
@patch("orxtra.services._run.start_run_from_file", new_callable=AsyncMock)
async def test_execute_workflow_calls_start_run(
    mock_start: AsyncMock,
    mock_storage_cls: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_start.return_value = uuid4()
    system = _patch_storage(mock_storage_cls)
    executor = ServicesActionExecutor(mock_pool, intent_prefix="test")

    await executor.execute_workflow(
        "/path/to/workflow.toml",
        {"key": "value"},
        [{"event_type": "task_completed"}],
    )

    mock_start.assert_awaited_once()
    call_args = mock_start.call_args
    # Positional order is pool, principal_storage, caller_principal, then intent
    # and path -- assert the pool and caller identity threaded through.
    assert call_args[0][0] is mock_pool
    assert call_args[0][2] is system
    intent = call_args[0][3]
    assert "test:" in intent
    assert "workflow" in intent


@patch("orxtra.identity.PgPrincipalStorage")
@patch("orxtra.services._run.start_run_from_file", new_callable=AsyncMock)
async def test_execute_workflow_intent_includes_event_count(
    mock_start: AsyncMock,
    mock_storage_cls: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_start.return_value = uuid4()
    _patch_storage(mock_storage_cls)
    executor = ServicesActionExecutor(mock_pool)

    events = [{"type": "a"}, {"type": "b"}, {"type": "c"}]
    await executor.execute_workflow("/w.toml", {}, events)

    intent = mock_start.call_args[0][3]
    assert "3 events" in intent


# -- execute_service_action --


async def test_execute_service_action_log() -> None:
    action = LogAction(message="test log", level="info")
    # LogAction does not need pool -- executes directly.
    await execute_service_action(action, [{"data": "x"}])


@patch("orxtra.identity.PgPrincipalStorage")
@patch("orxtra.services._run.start_run_from_file", new_callable=AsyncMock)
async def test_execute_service_action_workflow(
    mock_start: AsyncMock,
    mock_storage_cls: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    mock_start.return_value = uuid4()
    _patch_storage(mock_storage_cls)
    action = WorkflowAction(workflow_path="/test.toml", config={})

    await execute_service_action(
        action, [{"event_type": "x"}], pool=mock_pool,
    )

    mock_start.assert_awaited_once()


@patch("orxtra.identity.PgPrincipalStorage")
@patch("orxtra.services._events.TraceWriter")
async def test_execute_service_action_event(
    mock_writer_cls: AsyncMock,
    mock_storage_cls: AsyncMock,
    mock_pool: AsyncMock,
) -> None:
    system = _patch_storage(mock_storage_cls)
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(uuid4(), True))
    action = EventAction(event_type="custom_event", data={"key": "val"})

    await execute_service_action(
        action, [], pool=mock_pool,
    )

    # The re-fired event is attributed to the system principal.
    mock_writer.write_event.assert_awaited_once()
    _args, kwargs = mock_writer.write_event.call_args
    assert kwargs["principal_id"] == system.id


async def test_execute_service_action_workflow_without_pool() -> None:
    action = WorkflowAction(workflow_path="/test.toml", config={})
    with pytest.raises(RuntimeError, match="ActionExecutor"):
        await execute_service_action(action, [{"data": "x"}])


async def test_execute_service_action_event_without_pool() -> None:
    action = EventAction(event_type="test", data={})
    with pytest.raises(RuntimeError, match="event_fire_callback"):
        await execute_service_action(action, [])
