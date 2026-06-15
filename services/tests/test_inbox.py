from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from conftest import FakeRecord
from orxt.services._inbox import (
    get_inbox_item,
    list_inbox,
    reject_inbox_item,
    respond_to_inbox,
    skip_inbox_item,
)
from orxt.trace import InboxItem

if TYPE_CHECKING:
    from uuid import UUID


def _make_inbox_record(
    item_id: UUID, run_id: UUID, *, status: str = "pending"
) -> FakeRecord:
    return FakeRecord({
        "id": item_id,
        "run_id": run_id,
        "status": status,
        "decision_type": "approval",
        "question": "Should we proceed?",
        "options": [{"label": "yes"}, {"label": "no"}],
        "assumed_option": None,
        "work_proceeding": None,
        "contradiction_impact": None,
        "tags": ["test"],
        "deadline": None,
        "answer": None,
        "answer_event": None,
        "rejection_reason": None,
        "answered_at": None,
        "created_at": datetime(2024, 1, 1, tzinfo=UTC),
    })


@pytest.mark.asyncio
async def test_list_inbox_no_filter(
    mock_pool: AsyncMock, sample_run_id: UUID, sample_inbox_item: InboxItem
) -> None:
    with patch("orxt.services._inbox._read_inbox", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = [sample_inbox_item]

        result = await list_inbox(mock_pool, sample_run_id)

        assert result == [sample_inbox_item]
        mock_read.assert_called_once_with(mock_pool, sample_run_id, None)


@pytest.mark.asyncio
async def test_list_inbox_with_status(
    mock_pool: AsyncMock, sample_run_id: UUID, sample_inbox_item: InboxItem
) -> None:
    with patch("orxt.services._inbox._read_inbox", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = [sample_inbox_item]

        result = await list_inbox(mock_pool, sample_run_id, status="pending")

        assert result == [sample_inbox_item]
        mock_read.assert_called_once_with(mock_pool, sample_run_id, "pending")


@pytest.mark.asyncio
async def test_get_inbox_item(
    mock_pool: AsyncMock, sample_item_id: UUID, sample_run_id: UUID
) -> None:
    record = _make_inbox_record(sample_item_id, sample_run_id)
    mock_pool.fetchrow = AsyncMock(return_value=record)

    result = await get_inbox_item(mock_pool, sample_item_id)

    assert isinstance(result, InboxItem)
    assert result.id == sample_item_id
    assert result.question == "Should we proceed?"


@pytest.mark.asyncio
async def test_respond_to_inbox(
    mock_pool: AsyncMock, sample_item_id: UUID, sample_run_id: UUID
) -> None:
    record = _make_inbox_record(sample_item_id, sample_run_id, status="answered")
    mock_pool.fetchrow = AsyncMock(return_value=record)

    with patch("orxt.services._inbox.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        result = await respond_to_inbox(mock_pool, sample_item_id, "yes")

        mock_writer.answer_inbox_item.assert_called_once_with(sample_item_id, "yes")
        assert isinstance(result, InboxItem)


@pytest.mark.asyncio
async def test_skip_inbox_item_service(
    mock_pool: AsyncMock, sample_item_id: UUID, sample_run_id: UUID
) -> None:
    record = _make_inbox_record(sample_item_id, sample_run_id, status="skipped")
    mock_pool.fetchrow = AsyncMock(return_value=record)

    with patch("orxt.services._inbox.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        result = await skip_inbox_item(mock_pool, sample_item_id)

        mock_writer.skip_inbox_item.assert_called_once_with(sample_item_id)
        assert isinstance(result, InboxItem)


@pytest.mark.asyncio
async def test_reject_inbox_item_service(
    mock_pool: AsyncMock, sample_item_id: UUID, sample_run_id: UUID
) -> None:
    record = _make_inbox_record(sample_item_id, sample_run_id, status="rejected")
    mock_pool.fetchrow = AsyncMock(return_value=record)

    with patch("orxt.services._inbox.TraceWriter") as mock_writer_cls:
        mock_writer = AsyncMock()
        mock_writer_cls.return_value = mock_writer

        result = await reject_inbox_item(mock_pool, sample_item_id, "not relevant")

        mock_writer.reject_inbox_item.assert_called_once_with(
            sample_item_id, "not relevant"
        )
        assert isinstance(result, InboxItem)


@pytest.mark.asyncio
async def test_list_inbox_empty(
    mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    with patch("orxt.services._inbox._read_inbox", new_callable=AsyncMock) as mock_read:
        mock_read.return_value = []

        result = await list_inbox(mock_pool, sample_run_id)

        assert result == []


@pytest.mark.asyncio
async def test_get_inbox_item_not_found(
    mock_pool: AsyncMock, sample_item_id: UUID
) -> None:
    mock_pool.fetchrow = AsyncMock(return_value=None)

    with pytest.raises(ValueError, match="not found"):
        await get_inbox_item(mock_pool, sample_item_id)
