from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from uuid import UUID

import pytest
from orxt.trace import (
    InboxItem,
    IterationResult,
    NotepadEntry,
    RunReport,
    RunSummary,
    TaskAttempt,
    TaskSummary,
    list_iterations,
    list_runs,
    list_tasks,
    read_inbox,
    read_latest_attempt,
    read_notepad,
    read_run_report,
    read_task_attempt,
    read_transcript,
    search_transcript,
)

if TYPE_CHECKING:
    from .conftest import MockPool

RUN_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
TASK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
SESSION_ID = UUID("11111111-2222-3333-4444-555555555555")
ATTEMPT_ID = UUID("ffffffff-eeee-dddd-cccc-bbbbbbbbbbbb")
INBOX_ID = UUID("22222222-3333-4444-5555-666666666666")
NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


class TestListTasks:
    @pytest.mark.asyncio
    async def test_list_tasks(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "id": TASK_ID,
                "name": "deploy",
                "status": "active",
                "task_type": "step",
                "parent_task_id": None,
                "attempt_count": 2,
            },
        ])

        result = await list_tasks(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert len(result) == 1
        assert isinstance(result[0], TaskSummary)
        assert result[0].id == TASK_ID
        assert result[0].name == "deploy"
        assert result[0].status == "active"
        assert result[0].task_type == "step"
        assert result[0].parent_task_id is None
        assert result[0].attempt_count == 2

    @pytest.mark.asyncio
    async def test_list_tasks_empty(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([])

        result = await list_tasks(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert result == []


class TestReadTaskAttempt:
    @pytest.mark.asyncio
    async def test_read_task_attempt(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow({
            "id": ATTEMPT_ID,
            "task_id": TASK_ID,
            "attempt": 1,
            "status": "completed",
            "agent_output": "done",
            "structured_output": {"key": "val"},
            "check_result": None,
            "check_verdict": None,
            "session_id": SESSION_ID,
            "input_tokens": 100,
            "output_tokens": 50,
            "reasoning_tokens": 10,
            "cache_read_tokens": 5,
            "cache_write_tokens": 3,
            "cost_usd": Decimal("0.001"),
            "duration_seconds": 1.5,
        })

        result = await read_task_attempt(mock_pool, TASK_ID, 1)  # type: ignore[arg-type]

        assert result is not None
        assert isinstance(result, TaskAttempt)
        assert result.id == ATTEMPT_ID
        assert result.task_id == TASK_ID
        assert result.attempt == 1
        assert result.status == "completed"
        assert result.agent_output == "done"
        assert result.structured_output == {"key": "val"}
        assert result.check_result is None
        assert result.check_verdict is None
        assert result.session_id == SESSION_ID
        assert result.input_tokens == 100
        assert result.output_tokens == 50
        assert result.reasoning_tokens == 10
        assert result.cache_read_tokens == 5
        assert result.cache_write_tokens == 3
        assert result.cost_usd == Decimal("0.001")
        assert result.duration_seconds == 1.5

    @pytest.mark.asyncio
    async def test_read_task_attempt_not_found(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow(None)

        result = await read_task_attempt(mock_pool, TASK_ID, 1)  # type: ignore[arg-type]

        assert result is None


class TestReadLatestAttempt:
    @pytest.mark.asyncio
    async def test_read_latest_attempt(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow({
            "id": ATTEMPT_ID,
            "task_id": TASK_ID,
            "attempt": 3,
            "status": "completed",
            "agent_output": "final result",
            "structured_output": None,
            "check_result": None,
            "check_verdict": None,
            "session_id": SESSION_ID,
            "input_tokens": 200,
            "output_tokens": 100,
            "reasoning_tokens": 20,
            "cache_read_tokens": 10,
            "cache_write_tokens": 6,
            "cost_usd": Decimal("0.002"),
            "duration_seconds": 2.5,
        })

        result = await read_latest_attempt(mock_pool, TASK_ID)  # type: ignore[arg-type]

        assert result is not None
        assert isinstance(result, TaskAttempt)
        assert result.attempt == 3
        assert result.agent_output == "final result"

    @pytest.mark.asyncio
    async def test_read_latest_attempt_not_found(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow(None)

        result = await read_latest_attempt(mock_pool, TASK_ID)  # type: ignore[arg-type]

        assert result is None


class TestReadTranscript:
    @pytest.mark.asyncio
    async def test_read_transcript(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "turn": 1, "role": "user",
                "content": "hello",
                "tool_calls": None, "tokens": None,
                "created_at": NOW,
            },
            {
                "turn": 2, "role": "assistant",
                "content": "hi",
                "tool_calls": None, "tokens": None,
                "created_at": NOW,
            },
        ])

        result = await read_transcript(mock_pool, SESSION_ID)  # type: ignore[arg-type]

        assert len(result) == 2
        assert isinstance(result[0], dict)
        assert result[0]["turn"] == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hello"
        assert result[0]["tool_calls"] is None
        assert result[0]["tokens"] is None
        assert result[0]["created_at"] == NOW
        assert result[1]["turn"] == 2
        assert result[1]["role"] == "assistant"
        assert result[1]["content"] == "hi"


class TestSearchTranscript:
    @pytest.mark.asyncio
    async def test_search_transcript(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "turn": 1, "role": "user",
                "content": "hello world",
                "tool_calls": None, "tokens": None,
                "created_at": NOW,
            },
        ])

        result = await search_transcript(mock_pool, SESSION_ID, "hello")  # type: ignore[arg-type]

        assert len(result) == 1
        assert result[0]["content"] == "hello world"
        # Verify the SQL args include the ILIKE pattern.
        _sql, args = mock_pool.conn.executed[-1]
        assert args == (SESSION_ID, "%hello%")


class TestListRuns:
    @pytest.mark.asyncio
    async def test_list_runs(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "id": RUN_ID, "intent": "build",
                "status": "running",
                "created_at": NOW, "finished_at": None,
            },
        ])

        result = await list_runs(mock_pool)  # type: ignore[arg-type]

        assert len(result) == 1
        assert isinstance(result[0], RunSummary)
        assert result[0].id == RUN_ID
        assert result[0].intent == "build"
        assert result[0].status == "running"
        assert result[0].created_at == NOW
        assert result[0].finished_at is None


class TestReadInbox:
    @pytest.mark.asyncio
    async def test_read_inbox_all(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "id": INBOX_ID,
                "run_id": RUN_ID,
                "status": "pending",
                "decision_type": "approval",
                "question": "Proceed with deploy?",
                "options": [{"label": "yes"}, {"label": "no"}],
                "assumed_option": "yes",
                "work_proceeding": "deploying",
                "contradiction_impact": None,
                "tags": ["deploy", "prod"],
                "deadline": None,
                "answer": None,
                "answer_event": None,
                "rejection_reason": None,
                "answered_at": None,
                "created_at": NOW,
            },
        ])

        result = await read_inbox(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert len(result) == 1
        assert isinstance(result[0], InboxItem)
        assert result[0].id == INBOX_ID
        assert result[0].run_id == RUN_ID
        assert result[0].status == "pending"
        assert result[0].decision_type == "approval"
        assert result[0].question == "Proceed with deploy?"
        assert result[0].options == [{"label": "yes"}, {"label": "no"}]
        assert result[0].assumed_option == "yes"
        assert result[0].work_proceeding == "deploying"
        assert result[0].contradiction_impact is None
        assert result[0].tags == ["deploy", "prod"]
        assert result[0].deadline is None
        assert result[0].answer is None
        assert result[0].answer_event is None
        assert result[0].rejection_reason is None
        assert result[0].answered_at is None
        assert result[0].created_at == NOW

    @pytest.mark.asyncio
    async def test_read_inbox_with_status(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "id": INBOX_ID,
                "run_id": RUN_ID,
                "status": "pending",
                "decision_type": "approval",
                "question": "Proceed?",
                "options": [],
                "assumed_option": None,
                "work_proceeding": None,
                "contradiction_impact": None,
                "tags": [],
                "deadline": None,
                "answer": None,
                "answer_event": None,
                "rejection_reason": None,
                "answered_at": None,
                "created_at": NOW,
            },
        ])

        result = await read_inbox(mock_pool, RUN_ID, status="pending")  # type: ignore[arg-type]

        assert len(result) == 1
        # Verify the SQL args include the status filter.
        _sql, args = mock_pool.conn.executed[-1]
        assert args == (RUN_ID, "pending")


class TestReadNotepad:
    @pytest.mark.asyncio
    async def test_read_notepad(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetch([
            {
                "run_id": RUN_ID,
                "task_name": "deploy",
                "agent_name": "builder",
                "entry_type": "learning",
                "text": "learned something",
                "created_at": NOW,
            },
        ])

        result = await read_notepad(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert len(result) == 1
        assert isinstance(result[0], NotepadEntry)
        assert result[0].run_id == RUN_ID
        assert result[0].task_name == "deploy"
        assert result[0].agent_name == "builder"
        assert result[0].entry_type == "learning"
        assert result[0].text == "learned something"
        assert result[0].created_at == NOW


class TestReadRunReport:
    @pytest.mark.asyncio
    async def test_read_run_report(self, mock_pool: MockPool) -> None:
        # fetchrow: run data
        mock_pool.conn.queue_fetchrow({
            "id": RUN_ID,
            "intent": "build",
            "status": "running",
            "created_at": NOW,
            "finished_at": None,
            "autonomy_level": "full",
            "config_snapshot": {},
            "total_input_tokens": 100,
            "total_output_tokens": 50,
            "total_reasoning_tokens": 10,
            "total_cache_read_tokens": 5,
            "total_cache_write_tokens": 3,
            "total_cost_usd": Decimal("0.01"),
            "coherence_summary": None,
        })
        mock_pool.conn.queue_fetch([])  # tasks
        mock_pool.conn.queue_fetch([])  # decisions
        mock_pool.conn.queue_fetch([])  # constraints
        mock_pool.conn.queue_fetch([])  # assumptions

        result = await read_run_report(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert result is not None
        assert isinstance(result, RunReport)
        assert result.id == RUN_ID
        assert result.intent == "build"
        assert result.status == "running"
        assert result.created_at == NOW
        assert result.finished_at is None
        assert result.autonomy_level == "full"
        assert result.config_snapshot == {}
        assert result.total_input_tokens == 100
        assert result.total_output_tokens == 50
        assert result.total_reasoning_tokens == 10
        assert result.total_cache_read_tokens == 5
        assert result.total_cache_write_tokens == 3
        assert result.total_cost_usd == Decimal("0.01")
        assert result.coherence_summary is None
        assert result.tasks == []
        assert result.decisions == []
        assert result.constraints == []
        assert result.assumptions == []

    @pytest.mark.asyncio
    async def test_read_run_report_not_found(self, mock_pool: MockPool) -> None:
        mock_pool.conn.queue_fetchrow(None)

        result = await read_run_report(mock_pool, RUN_ID)  # type: ignore[arg-type]

        assert result is None


class TestListIterations:
    @pytest.mark.asyncio
    async def test_returns_iterations(self, mock_pool: MockPool) -> None:
        now = NOW
        task_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        iter_id = UUID("01234567-89ab-cdef-0123-456789abcdef")
        mock_pool.conn.queue_fetch([{
            "id": iter_id,
            "task_id": task_id,
            "iteration_index": 0,
            "item_value": {"key": "value"},
            "status": "completed",
            "output": "done",
            "structured_output": {"result": "ok"},
            "check_results": [{"passed": True}],
            "started_at": now,
            "finished_at": now,
        }])

        results = await list_iterations(mock_pool, task_id)  # type: ignore[arg-type]
        assert len(results) == 1
        assert isinstance(results[0], IterationResult)
        assert results[0].iteration_index == 0
        assert results[0].status == "completed"

    @pytest.mark.asyncio
    async def test_empty_iterations(self, mock_pool: MockPool) -> None:
        task_id = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
        results = await list_iterations(mock_pool, task_id)  # type: ignore[arg-type]
        assert results == []
