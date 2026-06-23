from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from .conftest import FakeRecord
from orxtra.services._trace import (
    get_notepad,
    get_task_attempts,
    get_transcript,
    list_tasks,
    query_events,
    search_transcript,
)
from orxtra.trace import NotepadEntry, TaskAttempt, TaskSummary


@pytest.mark.asyncio
async def test_list_tasks(
    mock_pool: AsyncMock, sample_run_id: UUID, sample_task_summary: TaskSummary
) -> None:
    with patch(
        "orxtra.services._trace._list_tasks", new_callable=AsyncMock
    ) as mock_list:
        mock_list.return_value = [sample_task_summary]

        result = await list_tasks(mock_pool, sample_run_id)

        assert result == [sample_task_summary]
        mock_list.assert_called_once_with(mock_pool, sample_run_id)


@pytest.mark.asyncio
async def test_get_task_attempts(
    mock_pool: AsyncMock, sample_task_id: UUID
) -> None:
    record = FakeRecord({
        "id": UUID("99999999-9999-9999-9999-999999999999"),
        "task_id": sample_task_id,
        "attempt": 1,
        "status": "completed",
        "agent_output": "done",
        "structured_output": None,
        "check_result": None,
        "check_verdict": None,
        "session_id": None,
        "input_tokens": 10,
        "output_tokens": 20,
        "reasoning_tokens": 5,
        "cache_read_tokens": 0,
        "cache_write_tokens": 0,
        "cost_usd": Decimal("0.001"),
        "duration_seconds": 1.5,
    })
    mock_pool.fetch = AsyncMock(return_value=[record])

    result = await get_task_attempts(mock_pool, sample_task_id)

    assert len(result) == 1
    assert isinstance(result[0], TaskAttempt)
    assert result[0].task_id == sample_task_id
    assert result[0].status == "completed"


@pytest.mark.asyncio
async def test_get_transcript(
    mock_pool: AsyncMock, sample_session_id: UUID
) -> None:
    transcript_data = [{"role": "user", "content": "hello"}]
    with patch(
        "orxtra.services._trace._read_transcript", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = transcript_data

        result = await get_transcript(mock_pool, sample_session_id)

        assert result == transcript_data
        mock_read.assert_called_once_with(mock_pool, sample_session_id)


@pytest.mark.asyncio
async def test_search_transcript(
    mock_pool: AsyncMock, sample_session_id: UUID
) -> None:
    search_data = [{"role": "assistant", "content": "matched"}]
    with patch(
        "orxtra.services._trace._search_transcript", new_callable=AsyncMock
    ) as mock_search:
        mock_search.return_value = search_data

        result = await search_transcript(mock_pool, sample_session_id, "matched")

        assert result == search_data
        mock_search.assert_called_once_with(mock_pool, sample_session_id, "matched")


@pytest.mark.asyncio
async def test_query_events_no_filter(
    mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    event_record = FakeRecord({
        "id": UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"),
        "run_id": sample_run_id,
        "task_id": None,
        "event_type": "started",
        "data": {"msg": "run started"},
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
    })
    mock_pool.fetch = AsyncMock(return_value=[event_record])

    result = await query_events(mock_pool, sample_run_id)

    assert len(result) == 1
    assert result[0]["event_type"] == "started"
    mock_pool.fetch.assert_called_once()
    call_args = mock_pool.fetch.call_args
    assert "run_id = $1" in call_args[0][0]


@pytest.mark.asyncio
async def test_query_events_with_type(
    mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_pool.fetch = AsyncMock(return_value=[])

    await query_events(mock_pool, sample_run_id, event_type="task_completed")

    call_args = mock_pool.fetch.call_args
    sql = call_args[0][0]
    assert "event_type = $2" in sql
    assert call_args[0][1] == sample_run_id
    assert call_args[0][2] == "task_completed"


@pytest.mark.asyncio
async def test_get_notepad(
    mock_pool: AsyncMock, sample_run_id: UUID, sample_notepad_entry: NotepadEntry
) -> None:
    with patch(
        "orxtra.services._trace._read_notepad", new_callable=AsyncMock
    ) as mock_read:
        mock_read.return_value = [sample_notepad_entry]

        result = await get_notepad(mock_pool, sample_run_id)

        assert result == [sample_notepad_entry]
        mock_read.assert_called_once_with(mock_pool, sample_run_id)


@pytest.mark.asyncio
async def test_query_events_empty(
    mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_pool.fetch = AsyncMock(return_value=[])

    result = await query_events(mock_pool, sample_run_id)

    assert result == []
