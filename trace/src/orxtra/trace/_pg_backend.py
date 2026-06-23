from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.trace import _lock, _reader, _recovery
from orxtra.trace._writer import TraceWriter

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    import asyncpg

    from orxtra.trace._types import (
        InboxItem,
        IterationResult,
        NotepadEntry,
        RunReport,
        RunSummary,
        TaskAttempt,
        TaskSummary,
    )


class PgBackend:
    """PostgreSQL implementation of StorageBackend.

    Thin wrapper that delegates to existing TraceWriter, reader functions,
    lock functions, and recovery functions. No new SQL.
    """

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
        self._writer = TraceWriter(pool, event_callback)

    # -- TaskStorage --

    async def create_task(
        self,
        run_id: UUID,
        parent_task_id: UUID | None,
        name: str,
        task_type: str,
        config: dict[str, Any] | None = None,
    ) -> UUID:
        return await self._writer.create_task(
            run_id, parent_task_id, name, task_type, config,
        )

    async def transition_task(
        self, task_id: UUID, new_status: str, reason: str | None = None,
    ) -> None:
        await self._writer.transition_task(task_id, new_status, reason)

    async def create_task_attempt(self, task_id: UUID, attempt: int) -> UUID:
        return await self._writer.create_task_attempt(task_id, attempt)

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
        await self._writer.complete_task_attempt(
            attempt_id, agent_output, structured_output, check_result,
            check_verdict, session_id, input_tokens, output_tokens,
            reasoning_tokens, cache_read_tokens, cache_write_tokens,
            cost_usd, duration_seconds,
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
        await self._writer.fail_task_attempt(
            attempt_id, error, session_id, input_tokens, output_tokens,
            reasoning_tokens, cache_read_tokens, cache_write_tokens,
            cost_usd, duration_seconds,
        )

    async def create_iteration(
        self,
        task_id: UUID,
        index: int,
        item_value: object,
    ) -> UUID:
        return await self._writer.create_iteration(task_id, index, item_value)

    async def complete_iteration(
        self,
        iteration_id: UUID,
        output: str | None,
        structured_output: dict[str, Any] | None,
        check_results: list[dict[str, Any]] | None,
    ) -> None:
        await self._writer.complete_iteration(
            iteration_id, output, structured_output, check_results,
        )

    async def fail_iteration(
        self,
        iteration_id: UUID,
        error: str,
    ) -> None:
        await self._writer.fail_iteration(iteration_id, error)

    # -- EventStorage --

    async def write_event(
        self,
        run_id: UUID,
        event_type: str,
        data: dict[str, Any],
        task_id: UUID | None = None,
    ) -> UUID:
        return await self._writer.write_event(run_id, event_type, data, task_id)

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
        await self._writer.write_transcript_entry(
            session_id, run_id, turn, role, content, tool_calls, tokens,
        )

    # -- RunStorage --

    async def create_run(
        self, intent: str, config: dict[str, Any], autonomy_level: str,
    ) -> UUID:
        return await self._writer.create_run(intent, config, autonomy_level)

    async def transition_run(
        self, run_id: UUID, new_status: str, reason: str | None = None,
    ) -> None:
        await self._writer.transition_run(run_id, new_status, reason)

    async def write_coherence_summary(self, run_id: UUID, summary: str) -> None:
        await self._writer.write_coherence_summary(run_id, summary)

    # -- RunControlStorage --

    async def subscribe_run_control(
        self, run_id: UUID, callback: Callable[[UUID, str], Awaitable[None]],
    ) -> None:
        await self._writer.subscribe_run_control(run_id, callback)

    async def unsubscribe_run_control(self, run_id: UUID) -> None:
        await self._writer.unsubscribe_run_control(run_id)

    # -- OverseerStorage --

    async def write_decision(
        self,
        run_id: UUID,
        decision_type: str,
        choice: str,
        rationale: str | None = None,
    ) -> UUID:
        return await self._writer.write_decision(
            run_id, decision_type, choice, rationale,
        )

    async def write_constraint(
        self,
        run_id: UUID,
        text: str,
        tier: str,
        kind: str,
        args: dict[str, Any] | None = None,
    ) -> UUID:
        return await self._writer.write_constraint(run_id, text, tier, kind, args)

    async def write_assumption(
        self,
        run_id: UUID,
        text: str,
        scope: str,
        inbox_item_id: UUID | None = None,
    ) -> UUID:
        return await self._writer.write_assumption(run_id, text, scope, inbox_item_id)

    async def write_lesson(
        self,
        run_id: UUID,
        text: str,
        relevance_tags: list[str],
        permanent: bool,
        source_files: list[str] | None = None,
    ) -> UUID:
        return await self._writer.write_lesson(
            run_id, text, relevance_tags, permanent, source_files,
        )

    async def update_workflow_status(
        self, workflow_id: UUID, current_step: str, health: str,
    ) -> None:
        await self._writer.update_workflow_status(workflow_id, current_step, health)

    async def write_context_diff(
        self, attempt_id: UUID, pre_refinement: str, refinement_diff: str,
    ) -> None:
        await self._writer.write_context_diff(
            attempt_id, pre_refinement, refinement_diff,
        )

    # -- InboxStorage --

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
        return await self._writer.create_inbox_item(
            run_id, decision_type, question, options, assumed_option,
            work_proceeding, contradiction_impact, tags, deadline,
            answer_event,
        )

    async def answer_inbox_item(self, item_id: UUID, answer: str) -> None:
        await self._writer.answer_inbox_item(item_id, answer)

    async def skip_inbox_item(self, item_id: UUID) -> None:
        await self._writer.skip_inbox_item(item_id)

    async def reject_inbox_item(self, item_id: UUID, reason: str) -> None:
        await self._writer.reject_inbox_item(item_id, reason)

    async def expire_inbox_item(self, item_id: UUID) -> None:
        await self._writer.expire_inbox_item(item_id)

    # -- NotepadStorage --

    async def write_notepad_entry(
        self,
        run_id: UUID,
        task_name: str,
        agent_name: str,
        entry_type: str,
        text: str,
    ) -> None:
        await self._writer.write_notepad_entry(
            run_id, task_name, agent_name, entry_type, text,
        )

    # -- StorageReader --

    async def list_tasks(self, run_id: UUID) -> list[TaskSummary]:
        return await _reader.list_tasks(self._pool, run_id)

    async def read_task_attempt(
        self, task_id: UUID, attempt: int,
    ) -> TaskAttempt | None:
        return await _reader.read_task_attempt(self._pool, task_id, attempt)

    async def read_latest_attempt(
        self, task_id: UUID,
    ) -> TaskAttempt | None:
        return await _reader.read_latest_attempt(self._pool, task_id)

    async def list_iterations(
        self, task_id: UUID,
    ) -> list[IterationResult]:
        return await _reader.list_iterations(self._pool, task_id)

    async def read_transcript(
        self, session_id: UUID,
    ) -> list[dict[str, Any]]:
        return await _reader.read_transcript(self._pool, session_id)

    async def search_transcript(
        self, session_id: UUID, query: str,
    ) -> list[dict[str, Any]]:
        return await _reader.search_transcript(self._pool, session_id, query)

    async def read_run_report(
        self, run_id: UUID,
    ) -> RunReport | None:
        return await _reader.read_run_report(self._pool, run_id)

    async def list_runs(self) -> list[RunSummary]:
        return await _reader.list_runs(self._pool)

    async def read_inbox(
        self, run_id: UUID, status: str | None = None,
    ) -> list[InboxItem]:
        return await _reader.read_inbox(self._pool, run_id, status)

    async def read_notepad(
        self, run_id: UUID,
    ) -> list[NotepadEntry]:
        return await _reader.read_notepad(self._pool, run_id)

    async def read_active_constraints(
        self, run_id: UUID,
    ) -> list[dict[str, Any]]:
        return await _reader.read_active_constraints(self._pool, run_id)

    async def read_task_attempts(
        self, task_id: UUID,
    ) -> list[TaskAttempt]:
        return await _reader.read_task_attempts(self._pool, task_id)

    async def query_events(
        self,
        run_id: UUID,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        return await _reader.query_events(
            self._pool, run_id, event_type, since, limit,
        )

    async def read_inbox_item(
        self, item_id: UUID,
    ) -> InboxItem | None:
        return await _reader.read_inbox_item(self._pool, item_id)

    async def read_run_config(
        self, run_id: UUID,
    ) -> dict[str, Any] | None:
        return await _reader.read_run_config(self._pool, run_id)

    async def read_session_token_counts(
        self, session_id: UUID,
    ) -> list[dict[str, Any]]:
        return await _reader.read_session_token_counts(self._pool, session_id)

    async def read_session_turn_count(
        self, session_id: UUID,
    ) -> int:
        return await _reader.read_session_turn_count(self._pool, session_id)

    async def query_relevant_lessons(
        self, tags: list[str],
    ) -> list[dict[str, Any]]:
        return await _reader.query_relevant_lessons(self._pool, tags)

    async def read_decisions(
        self, run_id: UUID, limit: int = 10,
    ) -> list[dict[str, Any]]:
        return await _reader.read_decisions(self._pool, run_id, limit)

    async def read_constraints(
        self, run_id: UUID, active_only: bool = True,
    ) -> list[dict[str, Any]]:
        return await _reader.read_constraints(self._pool, run_id, active_only)

    async def read_assumptions(
        self, run_id: UUID, status: str | None = None,
    ) -> list[dict[str, Any]]:
        return await _reader.read_assumptions(self._pool, run_id, status)

    async def query_lessons(
        self,
        run_id: UUID | None = None,
        tags: list[str] | None = None,
        permanent_only: bool = False,
    ) -> list[dict[str, Any]]:
        return await _reader.query_lessons(
            self._pool, run_id, tags, permanent_only,
        )

    async def read_workflow_status(
        self, workflow_id: UUID,
    ) -> dict[str, Any] | None:
        return await _reader.read_workflow_status(self._pool, workflow_id)

    # -- StorageLock --

    async def acquire_run_lock(self, run_id: UUID) -> None:
        await _lock.acquire_run_lock(self._pool, run_id)

    async def release_run_lock(self, run_id: UUID) -> None:
        await _lock.release_run_lock(self._pool, run_id)

    async def update_heartbeat(self, run_id: UUID) -> None:
        await _lock.update_heartbeat(self._pool, run_id)

    async def is_lock_stale(
        self, run_id: UUID, threshold_seconds: float = 300.0,
    ) -> bool:
        return await _lock.is_lock_stale(self._pool, run_id, threshold_seconds)

    # -- RecoveryOperations --

    async def reclaim_interrupted(self) -> int:
        return await _recovery.reclaim_interrupted(self._pool)

    async def reevaluate_blocked(self) -> list[UUID]:
        return await _recovery.reevaluate_blocked(self._pool)

    async def clean_orphaned(self) -> int:
        return await _recovery.clean_orphaned(self._pool)

    # -- KnowledgeHashStorage --

    async def write_knowledge_hash(self, run_id: UUID, path: str, file_hash: str) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO knowledge_hashes (id, run_id, path, file_hash)"
                " VALUES (uuid_generate_v7(), $1, $2, $3)"
                " ON CONFLICT (run_id, path) DO UPDATE"
                " SET file_hash = $3",
                run_id,
                path,
                file_hash,
            )

    async def read_knowledge_hashes(self, run_id: UUID) -> dict[str, str]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT path, file_hash FROM knowledge_hashes WHERE run_id = $1",
                run_id,
            )
            return {row["path"]: row["file_hash"] for row in rows}
