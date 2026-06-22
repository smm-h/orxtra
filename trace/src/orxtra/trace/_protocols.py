from __future__ import annotations

from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID

    from orxtra.trace._types import (
        InboxItem,
        IterationResult,
        NotepadEntry,
        RunReport,
        RunSummary,
        TaskAttempt,
        TaskSummary,
    )


@runtime_checkable
class TaskStorage(Protocol):
    """Task lifecycle operations."""

    async def create_task(
        self,
        run_id: UUID,
        parent_task_id: UUID | None,
        name: str,
        task_type: str,
        config: dict[str, Any] | None = None,
    ) -> UUID: ...

    async def transition_task(
        self, task_id: UUID, new_status: str, reason: str | None = None,
    ) -> None: ...

    async def create_task_attempt(self, task_id: UUID, attempt: int) -> UUID: ...

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
    ) -> None: ...

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
    ) -> None: ...

    async def create_iteration(
        self,
        task_id: UUID,
        index: int,
        item_value: object,
    ) -> UUID: ...

    async def complete_iteration(
        self,
        iteration_id: UUID,
        output: str | None,
        structured_output: dict[str, Any] | None,
        check_results: list[dict[str, Any]] | None,
    ) -> None: ...

    async def fail_iteration(
        self,
        iteration_id: UUID,
        error: str,
    ) -> None: ...


@runtime_checkable
class EventStorage(Protocol):
    """Event and transcript operations."""

    async def write_event(
        self,
        run_id: UUID,
        event_type: str,
        data: dict[str, Any],
        task_id: UUID | None = None,
    ) -> UUID: ...

    async def write_transcript_entry(
        self,
        session_id: UUID,
        run_id: UUID,
        turn: int,
        role: str,
        content: str,
        tool_calls: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> None: ...


@runtime_checkable
class RunStorage(Protocol):
    """Run lifecycle operations."""

    async def create_run(
        self, intent: str, config: dict[str, Any], autonomy_level: str,
    ) -> UUID: ...

    async def transition_run(
        self, run_id: UUID, new_status: str, reason: str | None = None,
    ) -> None: ...

    async def write_coherence_summary(self, run_id: UUID, summary: str) -> None: ...


@runtime_checkable
class RunControlStorage(Protocol):
    """Run control subscription operations."""

    async def subscribe_run_control(
        self, run_id: UUID, callback: Callable[[UUID, str], Awaitable[None]],
    ) -> None: ...

    async def unsubscribe_run_control(self, run_id: UUID) -> None: ...


@runtime_checkable
class OverseerStorage(Protocol):
    """Overseer state operations."""

    async def write_decision(
        self,
        run_id: UUID,
        decision_type: str,
        choice: str,
        rationale: str | None = None,
    ) -> UUID: ...

    async def write_constraint(
        self,
        run_id: UUID,
        text: str,
        tier: str,
        kind: str,
        args: dict[str, Any] | None = None,
    ) -> UUID: ...

    async def write_assumption(
        self,
        run_id: UUID,
        text: str,
        scope: str,
        inbox_item_id: UUID | None = None,
    ) -> UUID: ...

    async def write_lesson(
        self,
        run_id: UUID,
        text: str,
        relevance_tags: list[str],
        permanent: bool,
        source_files: list[str] | None = None,
    ) -> UUID: ...

    async def update_workflow_status(
        self, workflow_id: UUID, current_step: str, health: str,
    ) -> None: ...

    async def write_context_diff(
        self, attempt_id: UUID, pre_refinement: str, refinement_diff: str,
    ) -> None: ...


@runtime_checkable
class InboxStorage(Protocol):
    """Inbox operations."""

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
    ) -> UUID: ...

    async def answer_inbox_item(self, item_id: UUID, answer: str) -> None: ...

    async def skip_inbox_item(self, item_id: UUID) -> None: ...

    async def reject_inbox_item(self, item_id: UUID, reason: str) -> None: ...

    async def expire_inbox_item(self, item_id: UUID) -> None: ...


@runtime_checkable
class NotepadStorage(Protocol):
    """Notepad operations."""

    async def write_notepad_entry(
        self,
        run_id: UUID,
        task_name: str,
        agent_name: str,
        entry_type: str,
        text: str,
    ) -> None: ...


@runtime_checkable
class StorageReader(Protocol):
    """All read operations."""

    async def list_tasks(self, run_id: UUID) -> list[TaskSummary]: ...

    async def read_task_attempt(
        self, task_id: UUID, attempt: int,
    ) -> TaskAttempt | None: ...

    async def read_latest_attempt(
        self, task_id: UUID,
    ) -> TaskAttempt | None: ...

    async def list_iterations(
        self, task_id: UUID,
    ) -> list[IterationResult]: ...

    async def read_transcript(
        self, session_id: UUID,
    ) -> list[dict[str, Any]]: ...

    async def search_transcript(
        self, session_id: UUID, query: str,
    ) -> list[dict[str, Any]]: ...

    async def read_run_report(
        self, run_id: UUID,
    ) -> RunReport | None: ...

    async def list_runs(self) -> list[RunSummary]: ...

    async def read_inbox(
        self, run_id: UUID, status: str | None = None,
    ) -> list[InboxItem]: ...

    async def read_notepad(
        self, run_id: UUID,
    ) -> list[NotepadEntry]: ...

    async def read_active_constraints(
        self, run_id: UUID,
    ) -> list[dict[str, Any]]: ...

    async def read_task_attempts(
        self, task_id: UUID,
    ) -> list[TaskAttempt]: ...

    async def query_events(
        self,
        run_id: UUID,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]: ...

    async def read_inbox_item(
        self, item_id: UUID,
    ) -> InboxItem | None: ...

    async def read_run_config(
        self, run_id: UUID,
    ) -> dict[str, Any] | None: ...

    async def read_session_token_counts(
        self, session_id: UUID,
    ) -> list[dict[str, Any]]: ...

    async def read_session_turn_count(
        self, session_id: UUID,
    ) -> int: ...

    async def query_relevant_lessons(
        self, tags: list[str],
    ) -> list[dict[str, Any]]: ...

    async def read_decisions(
        self, run_id: UUID, limit: int = 10,
    ) -> list[dict[str, Any]]: ...

    async def read_constraints(
        self, run_id: UUID, active_only: bool = True,
    ) -> list[dict[str, Any]]: ...

    async def read_assumptions(
        self, run_id: UUID, status: str | None = None,
    ) -> list[dict[str, Any]]: ...

    async def query_lessons(
        self,
        run_id: UUID | None = None,
        tags: list[str] | None = None,
        permanent_only: bool = False,
    ) -> list[dict[str, Any]]: ...

    async def read_workflow_status(
        self, workflow_id: UUID,
    ) -> dict[str, Any] | None: ...


@runtime_checkable
class StorageLock(Protocol):
    """Run locking operations."""

    async def acquire_run_lock(self, run_id: UUID) -> None: ...

    async def release_run_lock(self, run_id: UUID) -> None: ...

    async def update_heartbeat(self, run_id: UUID) -> None: ...

    async def is_lock_stale(
        self, run_id: UUID, threshold_seconds: float = 300.0,
    ) -> bool: ...


@runtime_checkable
class RecoveryOperations(Protocol):
    """Crash recovery operations."""

    async def reclaim_interrupted(self) -> int: ...

    async def reevaluate_blocked(self) -> list[UUID]: ...

    async def clean_orphaned(self) -> int: ...


@runtime_checkable
class EventBus(Protocol):
    """Event notification (replaces LISTEN/NOTIFY)."""

    async def subscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None: ...

    async def publish(self, channel: str, payload: str) -> None: ...


@runtime_checkable
class StorageBackend(
    TaskStorage,
    EventStorage,
    RunStorage,
    RunControlStorage,
    OverseerStorage,
    InboxStorage,
    NotepadStorage,
    StorageReader,
    StorageLock,
    RecoveryOperations,
    Protocol,
):
    """Combined protocol for a complete storage backend.

    A StorageBackend provides all storage operations: task lifecycle,
    events, runs, overseer state, inbox, notepad, reads, locks, and
    crash recovery. EventBus is separate because it has different
    lifecycle (long-lived connections for LISTEN/NOTIFY).
    """

    ...
