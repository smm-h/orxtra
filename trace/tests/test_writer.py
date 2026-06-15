from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from orxt.trace import InvalidTransitionError, TraceWriter

if TYPE_CHECKING:
    from .conftest import MockPool

TEST_UUID = UUID("01234567-89ab-cdef-0123-456789abcdef")
TEST_UUID_2 = UUID("fedcba98-7654-3210-fedc-ba9876543210")

RUN_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
TASK_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")
ATTEMPT_ID = UUID("cccccccc-cccc-cccc-cccc-cccccccccccc")
SESSION_ID = UUID("dddddddd-dddd-dddd-dddd-dddddddddddd")
PARENT_TASK_ID = UUID("eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee")
INBOX_ITEM_ID = UUID("ffffffff-ffff-ffff-ffff-ffffffffffff")
WORKFLOW_ID = UUID("11111111-1111-1111-1111-111111111111")


class TestCreateRun:
    async def test_create_run(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.create_run(
                "build app", {"key": "val"}, "full",
            )

        assert result == TEST_UUID
        assert len(mock_pool.conn.executed) == 1
        sql, args = mock_pool.conn.executed[0]
        assert "insert into runs" in sql.lower()
        assert args == (
            TEST_UUID,
            "build app",
            json.dumps({"key": "val"}),
            "full",
        )


class TestTransitionRun:
    async def test_transition_run_valid(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow({"status": "created"})

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.transition_run(RUN_ID, "running")

        calls = mock_pool.conn.executed
        # fetchrow (SELECT), execute (UPDATE), execute (INSERT event)
        assert len(calls) == 3
        # First call is the fetchrow SELECT
        assert "select status from runs" in calls[0][0].lower()
        # Second call is the UPDATE
        assert "update runs set status" in calls[1][0].lower()
        # Third call is the INSERT event
        assert "insert into events" in calls[2][0].lower()

    async def test_transition_run_invalid(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow({"status": "created"})

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ), pytest.raises(InvalidTransitionError):
            await writer.transition_run(RUN_ID, "completed")

    async def test_transition_run_callback(
        self, mock_pool: MockPool,
    ) -> None:
        callback = AsyncMock()
        writer = TraceWriter(
            mock_pool,  # type: ignore[arg-type]
            event_callback=callback,
        )
        mock_pool.conn.queue_fetchrow({"status": "created"})

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.transition_run(RUN_ID, "running")

        callback.assert_called_once_with(
            TEST_UUID, RUN_ID, "run_transition",
            {"old_status": "created", "new_status": "running",
             "reason": None},
        )

    async def test_transition_run_not_found(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow(None)

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ), pytest.raises(ValueError, match="not found"):
            await writer.transition_run(RUN_ID, "running")


class TestCreateTask:
    async def test_create_task(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.create_task(
                RUN_ID, None, "deploy", "step",
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into tasks" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, None,
            "deploy", "step", json.dumps({}),
        )

    async def test_create_task_with_config(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.create_task(
                RUN_ID, None, "deploy", "step",
                config={"timeout": 30, "retries": 3},
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into tasks" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, None,
            "deploy", "step",
            json.dumps({"timeout": 30, "retries": 3}),
        )

    async def test_create_task_with_parent(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.create_task(
                RUN_ID, PARENT_TASK_ID, "deploy", "step",
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into tasks" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, PARENT_TASK_ID,
            "deploy", "step", json.dumps({}),
        )


class TestTransitionTask:
    async def test_transition_task_valid(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow(
            {"status": "created", "run_id": RUN_ID},
        )

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.transition_task(TASK_ID, "prechecking")

        calls = mock_pool.conn.executed
        assert len(calls) == 3
        assert "select status, run_id from tasks" in calls[0][0].lower()
        assert "update tasks set status" in calls[1][0].lower()
        assert "insert into events" in calls[2][0].lower()

    async def test_transition_task_callback(
        self, mock_pool: MockPool,
    ) -> None:
        callback = AsyncMock()
        writer = TraceWriter(
            mock_pool,  # type: ignore[arg-type]
            event_callback=callback,
        )
        mock_pool.conn.queue_fetchrow(
            {"status": "created", "run_id": RUN_ID},
        )

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.transition_task(TASK_ID, "prechecking")

        callback.assert_called_once_with(
            TEST_UUID, RUN_ID, "task_transition",
            {"task_id": str(TASK_ID), "old_status": "created",
             "new_status": "prechecking", "reason": None},
        )

    async def test_transition_task_invalid(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow(
            {"status": "created", "run_id": RUN_ID},
        )

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ), pytest.raises(InvalidTransitionError):
            await writer.transition_task(TASK_ID, "completed")


class TestTaskAttempt:
    async def test_create_task_attempt(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.create_task_attempt(TASK_ID, 1)

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into task_attempts" in sql.lower()
        assert args == (TEST_UUID, TASK_ID, 1)

    async def test_complete_task_attempt(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.complete_task_attempt(
            attempt_id=ATTEMPT_ID,
            agent_output="done",
            structured_output={"result": "ok"},
            check_result={"passed": True},
            check_verdict="pass",
            session_id=SESSION_ID,
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=20,
            cache_read_tokens=10,
            cache_write_tokens=5,
            cost_usd=Decimal("0.01"),
            duration_seconds=1.5,
        )

        sql, args = mock_pool.conn.executed[0]
        assert "update task_attempts set" in sql.lower()
        assert "status = 'completed'" in sql.lower()
        assert args == (
            "done",
            json.dumps({"result": "ok"}),
            json.dumps({"passed": True}),
            "pass",
            SESSION_ID,
            100,
            50,
            20,
            10,
            5,
            Decimal("0.01"),
            1.5,
            ATTEMPT_ID,
        )

    async def test_fail_task_attempt(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.fail_task_attempt(
            attempt_id=ATTEMPT_ID,
            error="something broke",
            session_id=SESSION_ID,
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=20,
            cache_read_tokens=10,
            cache_write_tokens=5,
            cost_usd=Decimal("0.005"),
            duration_seconds=0.8,
        )

        sql, args = mock_pool.conn.executed[0]
        assert "update task_attempts set" in sql.lower()
        assert "status = 'failed'" in sql.lower()
        assert args == (
            "something broke",
            SESSION_ID,
            100,
            50,
            20,
            10,
            5,
            Decimal("0.005"),
            0.8,
            ATTEMPT_ID,
        )


class TestWriteEvent:
    async def test_write_event(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_event(
                RUN_ID, "test_event", {"key": "value"},
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into events" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, None,
            "test_event", json.dumps({"key": "value"}),
        )

    async def test_write_event_with_task_id(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_event(
                RUN_ID, "test_event", {"key": "value"},
                task_id=TASK_ID,
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into events" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, TASK_ID,
            "test_event", json.dumps({"key": "value"}),
        )

    async def test_write_event_callback(
        self, mock_pool: MockPool,
    ) -> None:
        callback = AsyncMock()
        writer_with_cb = TraceWriter(
            mock_pool,  # type: ignore[arg-type]
            event_callback=callback,
        )

        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer_with_cb.write_event(
                RUN_ID, "test_event", {"key": "value"},
            )

        callback.assert_called_once_with(
            TEST_UUID, RUN_ID, "test_event", {"key": "value"},
        )


class TestTranscript:
    async def test_write_transcript_entry(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.write_transcript_entry(
                session_id=SESSION_ID,
                run_id=RUN_ID,
                turn=1,
                role="assistant",
                content="hello",
                tool_calls={"name": "read"},
                tokens={"input": 10},
            )

        sql, args = mock_pool.conn.executed[0]
        assert "insert into transcripts" in sql.lower()
        assert args == (
            TEST_UUID,
            SESSION_ID,
            RUN_ID,
            1,
            "assistant",
            "hello",
            json.dumps({"name": "read"}),
            json.dumps({"input": 10}),
        )


class TestNotepad:
    async def test_write_notepad_entry(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.write_notepad_entry(
                run_id=RUN_ID,
                task_name="deploy",
                agent_name="builder",
                entry_type="observation",
                text="server is ready",
            )

        sql, args = mock_pool.conn.executed[0]
        assert "insert into notepad_entries" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID,
            "deploy", "builder",
            "observation", "server is ready",
        )


class TestInbox:
    async def test_create_inbox_item(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        deadline = datetime(
            2026, 6, 14, 12, 0, 0, tzinfo=UTC,
        )
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.create_inbox_item(
                run_id=RUN_ID,
                decision_type="approval",
                question="proceed?",
                options=[{"label": "yes"}, {"label": "no"}],
                assumed_option="yes",
                work_proceeding="deploying",
                contradiction_impact="rollback needed",
                tags=["deploy", "prod"],
                deadline=deadline,
                answer_event="user_response",
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into inbox_items" in sql.lower()
        assert args == (
            TEST_UUID,
            RUN_ID,
            "approval",
            "proceed?",
            json.dumps([
                {"label": "yes"}, {"label": "no"},
            ]),
            "yes",
            "deploying",
            "rollback needed",
            json.dumps(["deploy", "prod"]),
            deadline,
            "user_response",
        )

    async def test_answer_inbox_item(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.answer_inbox_item(INBOX_ITEM_ID, "yes")

        sql, args = mock_pool.conn.executed[0]
        assert "update inbox_items" in sql.lower()
        assert "status = 'answered'" in sql.lower()
        assert args == ("yes", INBOX_ITEM_ID)

    async def test_skip_inbox_item(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.skip_inbox_item(INBOX_ITEM_ID)

        sql, args = mock_pool.conn.executed[0]
        assert "update inbox_items" in sql.lower()
        assert "status = 'skipped'" in sql.lower()
        assert args == (INBOX_ITEM_ID,)

    async def test_reject_inbox_item(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.reject_inbox_item(
            INBOX_ITEM_ID, "not relevant",
        )

        sql, args = mock_pool.conn.executed[0]
        assert "update inbox_items" in sql.lower()
        assert "status = 'rejected'" in sql.lower()
        assert args == ("not relevant", INBOX_ITEM_ID)

    async def test_expire_inbox_item(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.expire_inbox_item(INBOX_ITEM_ID)

        sql, args = mock_pool.conn.executed[0]
        assert "update inbox_items" in sql.lower()
        assert "status = 'expired'" in sql.lower()
        assert args == (INBOX_ITEM_ID,)


class TestContextDiff:
    async def test_write_context_diff(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            await writer.write_context_diff(
                ATTEMPT_ID, "original context", "diff content",
            )

        sql, args = mock_pool.conn.executed[0]
        assert "insert into context_diffs" in sql.lower()
        assert args == (
            TEST_UUID, ATTEMPT_ID,
            "original context", "diff content",
        )


class TestOverseerMemory:
    async def test_write_decision(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_decision(
                run_id=RUN_ID,
                decision_type="strategy",
                choice="deploy first",
                rationale="faster feedback",
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into decisions" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, "strategy",
            "deploy first", "faster feedback",
        )

    async def test_write_constraint(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_constraint(
                run_id=RUN_ID,
                text="no manual deploys",
                tier="hard",
                kind="no_removed_exports",
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into constraints" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID, "no manual deploys", "hard",
            "no_removed_exports", None,
        )

    async def test_write_assumption(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_assumption(
                run_id=RUN_ID,
                text="CI is green",
                scope="session",
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into assumptions" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID,
            "CI is green", "session", None,
        )

    async def test_write_assumption_with_inbox_item(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_assumption(
                run_id=RUN_ID,
                text="CI is green",
                scope="session",
                inbox_item_id=INBOX_ITEM_ID,
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into assumptions" in sql.lower()
        assert args == (
            TEST_UUID, RUN_ID,
            "CI is green", "session", INBOX_ITEM_ID,
        )

    async def test_write_lesson(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        with patch(
            "orxt.trace._writer.uuid6.uuid7",
            return_value=TEST_UUID,
        ):
            result = await writer.write_lesson(
                run_id=RUN_ID,
                text="always check CI",
                relevance_tags=["ci", "deploy"],
                permanent=True,
                source_files=["deploy.py"],
            )

        assert result == TEST_UUID
        sql, args = mock_pool.conn.executed[0]
        assert "insert into lessons" in sql.lower()
        assert args == (
            TEST_UUID,
            RUN_ID,
            "always check CI",
            json.dumps(["ci", "deploy"]),
            True,
            json.dumps(["deploy.py"]),
        )

    async def test_update_workflow_status(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.update_workflow_status(
            WORKFLOW_ID, "step_3", "healthy",
        )

        sql, args = mock_pool.conn.executed[0]
        assert "insert into overseer_workflow_status" in sql.lower()
        assert "on conflict" in sql.lower()
        assert args == (WORKFLOW_ID, "step_3", "healthy")

    async def test_write_coherence_summary(
        self, writer: TraceWriter, mock_pool: MockPool,
    ) -> None:
        await writer.write_coherence_summary(
            RUN_ID, "all consistent",
        )

        sql, args = mock_pool.conn.executed[0]
        assert "update runs set coherence_summary" in sql.lower()
        assert args == ("all consistent", RUN_ID)
