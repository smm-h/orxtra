from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest
from conftest import FakeRecord
from orxt.services._events import fire_event

if TYPE_CHECKING:
    from uuid import UUID


@pytest.mark.asyncio
async def test_fire_event_basic(
    mock_pool: AsyncMock, mock_conn: AsyncMock, sample_run_id: UUID
) -> None:
    mock_conn.fetchrow = AsyncMock(
        return_value=FakeRecord({"id": sample_run_id})
    )

    await fire_event(mock_pool, sample_run_id, "task_started")

    mock_conn.fetchrow.assert_called_once()
    mock_conn.execute.assert_called_once()
    call_args = mock_conn.execute.call_args
    assert call_args[0][1] == "orxt_events"
    notification = json.loads(call_args[0][2])
    assert notification["run_id"] == str(sample_run_id)
    assert notification["event"] == "task_started"
    assert notification["payload"] is None


@pytest.mark.asyncio
async def test_fire_event_with_payload(
    mock_pool: AsyncMock, mock_conn: AsyncMock, sample_run_id: UUID
) -> None:
    mock_conn.fetchrow = AsyncMock(
        return_value=FakeRecord({"id": sample_run_id})
    )
    payload = {"task_id": "abc", "status": "done"}

    await fire_event(mock_pool, sample_run_id, "task_completed", payload=payload)

    call_args = mock_conn.execute.call_args
    notification = json.loads(call_args[0][2])
    assert notification["payload"] == payload
    assert notification["event"] == "task_completed"


@pytest.mark.asyncio
async def test_fire_event_none_payload(
    mock_pool: AsyncMock, mock_conn: AsyncMock, sample_run_id: UUID
) -> None:
    mock_conn.fetchrow = AsyncMock(
        return_value=FakeRecord({"id": sample_run_id})
    )

    await fire_event(mock_pool, sample_run_id, "ping", payload=None)

    call_args = mock_conn.execute.call_args
    notification = json.loads(call_args[0][2])
    assert notification["payload"] is None


@pytest.mark.asyncio
async def test_fire_event_invalid_run(
    mock_pool: AsyncMock, mock_conn: AsyncMock, sample_run_id: UUID
) -> None:
    mock_conn.fetchrow = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await fire_event(mock_pool, sample_run_id, "task_started")

    mock_conn.execute.assert_not_called()
