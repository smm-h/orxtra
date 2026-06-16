from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from orxt.services._events import fire_event
from orxt.trace import TraceWriter

if TYPE_CHECKING:
    from uuid import UUID


@pytest.fixture
def mock_writer() -> AsyncMock:
    writer = AsyncMock(spec=TraceWriter)
    writer.write_event = AsyncMock(return_value=uuid4())
    return writer


@pytest.mark.asyncio
async def test_fire_event_basic(
    mock_writer: AsyncMock, sample_run_id: UUID
) -> None:
    result = await fire_event(mock_writer, sample_run_id, "task_started")

    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_started", {}
    )
    assert result == mock_writer.write_event.return_value


@pytest.mark.asyncio
async def test_fire_event_with_payload(
    mock_writer: AsyncMock, sample_run_id: UUID
) -> None:
    payload = {"task_id": "abc", "status": "done"}

    result = await fire_event(
        mock_writer, sample_run_id, "task_completed", payload=payload
    )

    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_completed", payload
    )
    assert result == mock_writer.write_event.return_value


@pytest.mark.asyncio
async def test_fire_event_none_payload(
    mock_writer: AsyncMock, sample_run_id: UUID
) -> None:
    await fire_event(mock_writer, sample_run_id, "ping", payload=None)

    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "ping", {}
    )


@pytest.mark.asyncio
async def test_fire_event_propagates_write_error(
    mock_writer: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer.write_event = AsyncMock(
        side_effect=asyncpg.ForeignKeyViolationError(
            "insert or update on table \"events\" violates"
            " foreign key constraint"
        )
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await fire_event(mock_writer, sample_run_id, "task_started")
