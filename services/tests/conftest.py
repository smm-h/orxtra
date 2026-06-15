from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import UUID

import pytest

from orxt.trace import (
    InboxItem,
    NotepadEntry,
    RunReport,
    RunSummary,
    TaskAttempt,
    TaskSummary,
)


class FakeRecord:
    """Mimics asyncpg.Record for dict() conversion in tests."""

    def __init__(self, data: dict[str, Any]) -> None:
        self._data = data

    def __iter__(self):
        return iter(self._data)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __getitem__(self, key: str) -> Any:
        return self._data[key]


@pytest.fixture
def mock_pool() -> AsyncMock:
    pool = AsyncMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__.return_value = conn
    pool.acquire.return_value.__aexit__.return_value = False
    pool.fetch = AsyncMock(return_value=[])
    pool.fetchrow = AsyncMock(return_value=None)
    pool.execute = AsyncMock()
    return pool


@pytest.fixture
def mock_conn(mock_pool: AsyncMock) -> AsyncMock:
    return mock_pool.acquire.return_value.__aenter__.return_value


@pytest.fixture
def sample_run_id() -> UUID:
    return UUID("12345678-1234-1234-1234-123456789abc")


@pytest.fixture
def sample_task_id() -> UUID:
    return UUID("abcdef01-abcd-abcd-abcd-abcdef012345")


@pytest.fixture
def sample_session_id() -> UUID:
    return UUID("fedcba98-fedc-fedc-fedc-fedcba987654")


@pytest.fixture
def sample_item_id() -> UUID:
    return UUID("11111111-2222-3333-4444-555555555555")


@pytest.fixture
def sample_run_summary(sample_run_id: UUID) -> RunSummary:
    return RunSummary(
        id=sample_run_id,
        intent="test intent",
        status="running",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
    )


@pytest.fixture
def sample_run_report(sample_run_id: UUID) -> RunReport:
    return RunReport(
        id=sample_run_id,
        intent="test intent",
        status="running",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        finished_at=None,
        autonomy_level="supervised",
        config_snapshot={"key": "value"},
        total_input_tokens=100,
        total_output_tokens=200,
        total_reasoning_tokens=50,
        total_cache_read_tokens=10,
        total_cache_write_tokens=5,
        total_cost_usd=Decimal("0.01"),
        coherence_summary=None,
        tasks=[],
        decisions=[],
        constraints=[],
        assumptions=[],
    )


@pytest.fixture
def sample_task_summary(sample_task_id: UUID) -> TaskSummary:
    return TaskSummary(
        id=sample_task_id,
        name="test-task",
        status="pending",
        task_type="agent",
        parent_task_id=None,
        attempt_count=0,
    )


@pytest.fixture
def sample_task_attempt(sample_task_id: UUID) -> TaskAttempt:
    return TaskAttempt(
        id=UUID("99999999-9999-9999-9999-999999999999"),
        task_id=sample_task_id,
        attempt=1,
        status="completed",
        agent_output="done",
        structured_output=None,
        check_result=None,
        check_verdict=None,
        session_id=None,
        input_tokens=10,
        output_tokens=20,
        reasoning_tokens=5,
        cache_read_tokens=0,
        cache_write_tokens=0,
        cost_usd=Decimal("0.001"),
        duration_seconds=1.5,
    )


@pytest.fixture
def sample_inbox_item(sample_item_id: UUID, sample_run_id: UUID) -> InboxItem:
    return InboxItem(
        id=sample_item_id,
        run_id=sample_run_id,
        status="pending",
        decision_type="approval",
        question="Should we proceed?",
        options=[{"label": "yes"}, {"label": "no"}],
        assumed_option=None,
        work_proceeding=None,
        contradiction_impact=None,
        tags=["test"],
        deadline=None,
        answer=None,
        answer_event=None,
        rejection_reason=None,
        answered_at=None,
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )


@pytest.fixture
def sample_notepad_entry(sample_run_id: UUID) -> NotepadEntry:
    return NotepadEntry(
        run_id=sample_run_id,
        task_name="task-1",
        agent_name="agent-1",
        entry_type="observation",
        text="Found something interesting",
        created_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
    )
