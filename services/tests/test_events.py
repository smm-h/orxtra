from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from orxtra.services._events import fire_event

if TYPE_CHECKING:
    from uuid import UUID


@pytest.fixture
def mock_pool() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_writer() -> AsyncMock:
    writer = AsyncMock()
    writer.write_event = AsyncMock(return_value=uuid4())
    return writer


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_basic(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=uuid4())

    result = await fire_event(mock_pool, sample_run_id, "task_started")

    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_started", {}
    )
    assert result == mock_writer.write_event.return_value


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_with_payload(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=uuid4())
    payload = {"task_id": "abc", "status": "done"}

    result = await fire_event(
        mock_pool, sample_run_id, "task_completed", payload=payload
    )

    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_completed", payload
    )
    assert result == mock_writer.write_event.return_value


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_none_payload(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=uuid4())

    await fire_event(mock_pool, sample_run_id, "ping", payload=None)

    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "ping", {}
    )


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_propagates_write_error(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(
        side_effect=asyncpg.ForeignKeyViolationError(
            'insert or update on table "events" violates'
            " foreign key constraint"
        )
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await fire_event(mock_pool, sample_run_id, "task_started")
