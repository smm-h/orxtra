from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from orxtra.notepad import NotepadEntry, read_notepad

if TYPE_CHECKING:
    from .conftest import MockPool

RUN_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


class TestReadNotepad:
    @pytest.mark.asyncio
    async def test_returns_entries_ordered_by_created_at(
        self, mock_pool: MockPool,
    ) -> None:
        earlier = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)
        later = datetime(2025, 6, 1, 12, 5, 0, tzinfo=UTC)
        mock_pool.conn.queue_fetch([
            {
                "run_id": RUN_ID,
                "task_name": "research",
                "agent_name": "researcher",
                "entry_type": "learning",
                "text": "first finding",
                "created_at": earlier,
            },
            {
                "run_id": RUN_ID,
                "task_name": "generate",
                "agent_name": "generator",
                "entry_type": "decision",
                "text": "chose approach A",
                "created_at": later,
            },
        ])

        result = await read_notepad(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert len(result) == 2
        assert isinstance(result[0], NotepadEntry)
        assert isinstance(result[1], NotepadEntry)
        assert result[0].task_name == "research"
        assert result[0].agent_name == "researcher"
        assert result[0].entry_type == "learning"
        assert result[0].text == "first finding"
        assert result[0].created_at == earlier
        assert result[1].task_name == "generate"
        assert result[1].text == "chose approach A"
        assert result[1].created_at == later

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_no_entries(
        self, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetch([])

        result = await read_notepad(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert result == []

    @pytest.mark.asyncio
    async def test_filters_by_run_id(
        self, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetch([])

        await read_notepad(mock_pool, RUN_ID)  # type: ignore[arg-type]

        _sql, args = mock_pool.conn.executed[-1]
        assert RUN_ID in args
