from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import uuid6

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    import asyncpg

from orxtra.trace._transitions import (
    InvalidTransitionError,
    validate_run_transition,
    validate_task_transition,
)


class TraceWriter:
    def __init__(
        self,
        pool: asyncpg.Pool,
        event_callback: Callable[
            [UUID, UUID, str, dict[str, Any]],
            Awaitable[None],
        ]
        | None = None,
    ) -> None:
        self._pool = pool
        self._event_callback = event_callback
        self._control_callbacks: dict[UUID, Callable[[UUID, str], Awaitable[None]]] = {}

    async def subscribe_run_control(
        self, run_id: UUID, callback: Callable[[UUID, str], Awaitable[None]],
    ) -> None:
        """Register a callback for run control signals (pause/abort).

        Handles the startup race: if the run is already paused or aborted
        when subscribing, the callback fires immediately.
        """
        self._control_callbacks[run_id] = callback
        current = await self._pool.fetchval(
            "SELECT status FROM runs WHERE id = $1", run_id,
        )
        if current in ("paused", "aborted"):
            await callback(run_id, current)

    async def unsubscribe_run_control(self, run_id: UUID) -> None:
        self._control_callbacks.pop(run_id, None)

    async def create_run(
        self, intent: str, config: dict[str, Any], autonomy_level: str
    ) -> UUID:
        run_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO runs (id, intent, config_snapshot, autonomy_level)"
                " VALUES ($1, $2, $3, $4)",
                run_id,
                intent,
                json.dumps(config),
                autonomy_level,
            )
        return run_id

    async def transition_run(
        self, run_id: UUID, new_status: str, reason: str | None = None
    ) -> None:
        event_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT status FROM runs WHERE id = $1 FOR UPDATE", run_id
            )
            if row is None:
                msg = f"run {run_id} not found"
                raise ValueError(msg)
            old_status: str = row["status"]
            validate_run_transition(old_status, new_status)
            await conn.execute(
                "UPDATE runs SET status = $1,"
                " finished_at = CASE WHEN $1 IN ('completed', 'failed', 'aborted')"
                " THEN now() ELSE finished_at END"
                " WHERE id = $2",
                new_status,
                run_id,
            )
            event_data = {
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            }
            await conn.execute(
                "INSERT INTO events (id, run_id, task_id, event_type, data)"
                " VALUES ($1, $2, $3, $4, $5)",
                event_id,
                run_id,
                None,
                "run_transition",
                json.dumps(event_data),
            )
        if self._event_callback is not None:
            await self._event_callback(event_id, run_id, "run_transition", event_data)
        if run_id in self._control_callbacks:
            await self._control_callbacks[run_id](run_id, new_status)

    async def create_task(
        self,
        run_id: UUID,
        parent_task_id: UUID | None,
        name: str,
        task_type: str,
        config: dict[str, Any] | None = None,
    ) -> UUID:
        task_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO tasks"
                " (id, run_id, parent_task_id, name, task_type, config)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                task_id,
                run_id,
                parent_task_id,
                name,
                task_type,
                json.dumps(config or {}),
            )
        return task_id

    async def transition_task(
        self, task_id: UUID, new_status: str, reason: str | None = None
    ) -> None:
        event_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT status, run_id FROM tasks WHERE id = $1 FOR UPDATE",
                task_id,
            )
            if row is None:
                msg = f"task {task_id} not found"
                raise ValueError(msg)
            old_status: str = row["status"]
            run_id: UUID = row["run_id"]
            validate_task_transition(old_status, new_status)
            await conn.execute(
                "UPDATE tasks SET status = $1 WHERE id = $2",
                new_status,
                task_id,
            )
            event_data = {
                "task_id": str(task_id),
                "old_status": old_status,
                "new_status": new_status,
                "reason": reason,
            }
            await conn.execute(
                "INSERT INTO events (id, run_id, task_id, event_type, data)"
                " VALUES ($1, $2, $3, $4, $5)",
                event_id,
                run_id,
                task_id,
                "task_transition",
                json.dumps(event_data),
            )
        if self._event_callback is not None:
            await self._event_callback(event_id, run_id, "task_transition", event_data)

    async def create_task_attempt(self, task_id: UUID, attempt: int) -> UUID:
        attempt_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO task_attempts (id, task_id, attempt)"
                " VALUES ($1, $2, $3)",
                attempt_id,
                task_id,
                attempt,
            )
        return attempt_id

    async def complete_task_attempt(
        self,
        attempt_id: UUID,
        agent_output: str,
        structured_output: dict[str, Any] | None,
        check_result: dict[str, Any] | None,
        check_verdict: str | None,
        session_id: UUID | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: Decimal,
        duration_seconds: float,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE task_attempts SET"
                " status = 'completed',"
                " agent_output = $1,"
                " structured_output = $2,"
                " check_result = $3,"
                " check_verdict = $4,"
                " session_id = $5,"
                " input_tokens = $6,"
                " output_tokens = $7,"
                " reasoning_tokens = $8,"
                " cache_read_tokens = $9,"
                " cache_write_tokens = $10,"
                " cost_usd = $11,"
                " duration_seconds = $12"
                " WHERE id = $13",
                agent_output,
                json.dumps(structured_output)
                if structured_output is not None
                else None,
                json.dumps(check_result)
                if check_result is not None
                else None,
                check_verdict,
                session_id,
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                duration_seconds,
                attempt_id,
            )

    async def fail_task_attempt(
        self,
        attempt_id: UUID,
        error: str,
        session_id: UUID | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: Decimal,
        duration_seconds: float,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE task_attempts SET"
                " status = 'failed',"
                " agent_output = $1,"
                " session_id = $2,"
                " input_tokens = $3,"
                " output_tokens = $4,"
                " reasoning_tokens = $5,"
                " cache_read_tokens = $6,"
                " cache_write_tokens = $7,"
                " cost_usd = $8,"
                " duration_seconds = $9"
                " WHERE id = $10",
                error,
                session_id,
                input_tokens,
                output_tokens,
                reasoning_tokens,
                cache_read_tokens,
                cache_write_tokens,
                cost_usd,
                duration_seconds,
                attempt_id,
            )

    async def write_event(
        self,
        run_id: UUID,
        event_type: str,
        data: dict[str, Any],
        task_id: UUID | None = None,
    ) -> UUID:
        event_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO events (id, run_id, task_id, event_type, data)"
                " VALUES ($1, $2, $3, $4, $5)",
                event_id,
                run_id,
                task_id,
                event_type,
                json.dumps(data),
            )
        if self._event_callback is not None:
            await self._event_callback(event_id, run_id, event_type, data)
        return event_id

    async def write_transcript_entry(
        self,
        session_id: UUID,
        run_id: UUID,
        turn: int,
        role: str,
        content: str,
        tool_calls: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO transcripts"
                " (id, session_id, run_id, turn, role,"
                " content, tool_calls, tokens)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8)",
                uuid6.uuid7(),
                session_id,
                run_id,
                turn,
                role,
                content,
                json.dumps(tool_calls) if tool_calls is not None else None,
                json.dumps(tokens) if tokens is not None else None,
            )

    async def write_notepad_entry(
        self,
        run_id: UUID,
        task_name: str,
        agent_name: str,
        entry_type: str,
        text: str,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO notepad_entries"
                " (id, run_id, task_name, agent_name,"
                " entry_type, text)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                uuid6.uuid7(),
                run_id,
                task_name,
                agent_name,
                entry_type,
                text,
            )

    async def create_inbox_item(
        self,
        run_id: UUID,
        decision_type: str,
        question: str,
        options: list[dict[str, Any]],
        assumed_option: str | None,
        work_proceeding: str | None,
        contradiction_impact: str | None,
        tags: list[str] | None = None,
        deadline: datetime | None = None,
        answer_event: str | None = None,
    ) -> UUID:
        item_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO inbox_items"
                " (id, run_id, decision_type, question, options,"
                " assumed_option, work_proceeding,"
                " contradiction_impact,"
                " tags, deadline, answer_event)"
                " VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11)",
                item_id,
                run_id,
                decision_type,
                question,
                json.dumps(options),
                assumed_option,
                work_proceeding,
                contradiction_impact,
                json.dumps(tags) if tags is not None else None,
                deadline,
                answer_event,
            )
        return item_id

    async def answer_inbox_item(self, item_id: UUID, answer: str) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "UPDATE inbox_items SET status = 'answered',"
                " answer = $1, answered_at = now()"
                " WHERE id = $2 AND status = 'pending'",
                answer,
                item_id,
            )
            if status == "UPDATE 0":
                current = await conn.fetchval(
                    "SELECT status FROM inbox_items WHERE id = $1",
                    item_id,
                )
                msg = (
                    f"cannot transition inbox item {item_id}"
                    f" from {current!r} to 'answered':"
                    " only 'pending' items can be answered"
                )
                raise InvalidTransitionError(msg)

    async def skip_inbox_item(self, item_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "UPDATE inbox_items SET status = 'skipped'"
                " WHERE id = $1 AND status = 'pending'",
                item_id,
            )
            if status == "UPDATE 0":
                current = await conn.fetchval(
                    "SELECT status FROM inbox_items WHERE id = $1",
                    item_id,
                )
                msg = (
                    f"cannot transition inbox item {item_id}"
                    f" from {current!r} to 'skipped':"
                    " only 'pending' items can be skipped"
                )
                raise InvalidTransitionError(msg)

    async def reject_inbox_item(self, item_id: UUID, reason: str) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "UPDATE inbox_items SET status = 'rejected',"
                " rejection_reason = $1"
                " WHERE id = $2 AND status = 'pending'",
                reason,
                item_id,
            )
            if status == "UPDATE 0":
                current = await conn.fetchval(
                    "SELECT status FROM inbox_items WHERE id = $1",
                    item_id,
                )
                msg = (
                    f"cannot transition inbox item {item_id}"
                    f" from {current!r} to 'rejected':"
                    " only 'pending' items can be rejected"
                )
                raise InvalidTransitionError(msg)

    async def expire_inbox_item(self, item_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "UPDATE inbox_items SET status = 'expired'"
                " WHERE id = $1 AND status = 'pending'",
                item_id,
            )
            if status == "UPDATE 0":
                current = await conn.fetchval(
                    "SELECT status FROM inbox_items WHERE id = $1",
                    item_id,
                )
                msg = (
                    f"cannot transition inbox item {item_id}"
                    f" from {current!r} to 'expired':"
                    " only 'pending' items can be expired"
                )
                raise InvalidTransitionError(msg)

    async def write_context_diff(
        self, attempt_id: UUID, pre_refinement: str, refinement_diff: str
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO context_diffs"
                " (id, attempt_id, pre_refinement,"
                " refinement_diff)"
                " VALUES ($1, $2, $3, $4)",
                uuid6.uuid7(),
                attempt_id,
                pre_refinement,
                refinement_diff,
            )

    async def write_decision(
        self,
        run_id: UUID,
        decision_type: str,
        choice: str,
        rationale: str | None = None,
    ) -> UUID:
        decision_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO decisions"
                " (id, run_id, decision_type, choice,"
                " rationale)"
                " VALUES ($1, $2, $3, $4, $5)",
                decision_id,
                run_id,
                decision_type,
                choice,
                rationale,
            )
        return decision_id

    async def write_constraint(
        self,
        run_id: UUID,
        text: str,
        tier: str,
        kind: str,
        args: dict[str, Any] | None = None,
    ) -> UUID:
        constraint_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO constraints (id, run_id, text, tier, kind, args)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                constraint_id,
                run_id,
                text,
                tier,
                kind,
                json.dumps(args) if args is not None else None,
            )
        return constraint_id

    async def write_assumption(
        self,
        run_id: UUID,
        text: str,
        scope: str,
        inbox_item_id: UUID | None = None,
    ) -> UUID:
        assumption_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO assumptions"
                " (id, run_id, text, scope, inbox_item_id)"
                " VALUES ($1, $2, $3, $4, $5)",
                assumption_id,
                run_id,
                text,
                scope,
                inbox_item_id,
            )
        return assumption_id

    async def write_lesson(
        self,
        run_id: UUID,
        text: str,
        relevance_tags: list[str],
        permanent: bool,
        source_files: list[str] | None = None,
    ) -> UUID:
        lesson_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO lessons"
                " (id, run_id, text, relevance_tags,"
                " permanent, source_files)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                lesson_id,
                run_id,
                text,
                json.dumps(relevance_tags),
                permanent,
                json.dumps(source_files) if source_files is not None else None,
            )
        return lesson_id

    async def create_iteration(
        self,
        task_id: UUID,
        index: int,
        item_value: object,
    ) -> UUID:
        iteration_id = uuid6.uuid7()
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO task_iterations"
                " (id, task_id, iteration_index, item_value)"
                " VALUES ($1, $2, $3, $4)",
                iteration_id,
                task_id,
                index,
                json.dumps(item_value),
            )
        return iteration_id

    async def complete_iteration(
        self,
        iteration_id: UUID,
        output: str | None,
        structured_output: dict[str, Any] | None,
        check_results: list[dict[str, Any]] | None,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE task_iterations SET"
                " status = 'completed',"
                " output = $1,"
                " structured_output = $2,"
                " check_results = $3,"
                " finished_at = now()"
                " WHERE id = $4",
                output,
                json.dumps(structured_output)
                if structured_output is not None
                else None,
                json.dumps(check_results)
                if check_results is not None
                else None,
                iteration_id,
            )

    async def fail_iteration(
        self,
        iteration_id: UUID,
        error: str,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE task_iterations SET"
                " status = 'failed',"
                " output = $1,"
                " finished_at = now()"
                " WHERE id = $2",
                error,
                iteration_id,
            )

    async def update_workflow_status(
        self, workflow_id: UUID, current_step: str, health: str
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO overseer_workflow_status"
                " (workflow_id, current_step, health,"
                " updated_at)"
                " VALUES ($1, $2, $3, now())"
                " ON CONFLICT (workflow_id) DO UPDATE"
                " SET current_step = $2, health = $3,"
                " updated_at = now()",
                workflow_id,
                current_step,
                health,
            )

    async def write_coherence_summary(self, run_id: UUID, summary: str) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "UPDATE runs SET coherence_summary = $1"
                " WHERE id = $2",
                summary,
                run_id,
            )
