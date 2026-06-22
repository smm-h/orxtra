from __future__ import annotations

import json
import time
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import uuid6

from orxtra.trace._transitions import (
    InvalidTransitionError,
    validate_run_transition,
    validate_task_transition,
)
from orxtra.trace._types import (
    InboxItem,
    IterationResult,
    NotepadEntry,
    RunReport,
    RunSummary,
    TaskAttempt,
    TaskSummary,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from datetime import datetime
    from decimal import Decimal
    from uuid import UUID


class InMemoryBackend:
    """In-memory implementation of StorageBackend for tests and lightweight use."""

    def __init__(self) -> None:
        # Runs: id -> row dict
        self._runs: dict[UUID, dict[str, Any]] = {}
        # Tasks: id -> row dict
        self._tasks: dict[UUID, dict[str, Any]] = {}
        # Task attempts: id -> row dict
        self._task_attempts: dict[UUID, dict[str, Any]] = {}
        # Events: ordered list of row dicts
        self._events: list[dict[str, Any]] = []
        # Transcripts: ordered list of row dicts
        self._transcripts: list[dict[str, Any]] = []
        # Notepad entries: ordered list of row dicts
        self._notepad_entries: list[dict[str, Any]] = []
        # Inbox items: id -> row dict
        self._inbox_items: dict[UUID, dict[str, Any]] = {}
        # Decisions: ordered list of row dicts
        self._decisions: list[dict[str, Any]] = []
        # Constraints: ordered list of row dicts
        self._constraints: list[dict[str, Any]] = []
        # Assumptions: ordered list of row dicts
        self._assumptions: list[dict[str, Any]] = []
        # Lessons: ordered list of row dicts
        self._lessons: list[dict[str, Any]] = []
        # Context diffs: ordered list of row dicts
        self._context_diffs: list[dict[str, Any]] = []
        # Workflow status: workflow_id -> row dict
        self._workflow_status: dict[UUID, dict[str, Any]] = {}
        # Iterations: id -> row dict
        self._iterations: dict[UUID, dict[str, Any]] = {}
        # Locks: run_id -> locked flag
        self._run_locks: dict[UUID, bool] = {}
        # Heartbeats: run_id -> timestamp (monotonic)
        self._heartbeats: dict[UUID, float] = {}
        # Run control callbacks: run_id -> callback
        self._control_callbacks: dict[UUID, Callable[[UUID, str], Awaitable[None]]] = {}
        # Event callback (mirrors TraceWriter pattern)
        self._event_callback: (
            Callable[[UUID, UUID, str, dict[str, Any]], Awaitable[None]] | None
        ) = None

    # ── TaskStorage ──

    async def create_task(
        self,
        run_id: UUID,
        parent_task_id: UUID | None,
        name: str,
        task_type: str,
        config: dict[str, Any] | None = None,
    ) -> UUID:
        task_id = uuid6.uuid7()
        now = _now()
        self._tasks[task_id] = {
            "id": task_id,
            "run_id": run_id,
            "parent_task_id": parent_task_id,
            "name": name,
            "task_type": task_type,
            "config": json.dumps(config or {}),
            "status": "created",
            "created_at": now,
        }
        return task_id

    async def transition_task(
        self, task_id: UUID, new_status: str, reason: str | None = None,
    ) -> None:
        task = self._tasks.get(task_id)
        if task is None:
            msg = f"task {task_id} not found"
            raise ValueError(msg)
        old_status: str = task["status"]
        validate_task_transition(old_status, new_status)
        task["status"] = new_status
        event_id = uuid6.uuid7()
        run_id: UUID = task["run_id"]
        event_data = {
            "task_id": str(task_id),
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        }
        self._events.append({
            "id": event_id,
            "run_id": run_id,
            "task_id": task_id,
            "event_type": "task_transition",
            "data": event_data,
            "created_at": _now(),
        })
        if self._event_callback is not None:
            await self._event_callback(event_id, run_id, "task_transition", event_data)

    async def create_task_attempt(self, task_id: UUID, attempt: int) -> UUID:
        attempt_id = uuid6.uuid7()
        self._task_attempts[attempt_id] = {
            "id": attempt_id,
            "task_id": task_id,
            "attempt": attempt,
            "status": "running",
            "agent_output": None,
            "structured_output": None,
            "check_result": None,
            "check_verdict": None,
            "session_id": None,
            "input_tokens": 0,
            "output_tokens": 0,
            "reasoning_tokens": 0,
            "cache_read_tokens": 0,
            "cache_write_tokens": 0,
            "cost_usd": Decimal(0),
            "duration_seconds": None,
        }
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
        att = self._task_attempts[attempt_id]
        att["status"] = "completed"
        att["agent_output"] = agent_output
        att["structured_output"] = structured_output
        att["check_result"] = check_result
        att["check_verdict"] = check_verdict
        att["session_id"] = session_id
        att["input_tokens"] = input_tokens
        att["output_tokens"] = output_tokens
        att["reasoning_tokens"] = reasoning_tokens
        att["cache_read_tokens"] = cache_read_tokens
        att["cache_write_tokens"] = cache_write_tokens
        att["cost_usd"] = cost_usd
        att["duration_seconds"] = duration_seconds

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
        att = self._task_attempts[attempt_id]
        att["status"] = "failed"
        att["agent_output"] = error
        att["session_id"] = session_id
        att["input_tokens"] = input_tokens
        att["output_tokens"] = output_tokens
        att["reasoning_tokens"] = reasoning_tokens
        att["cache_read_tokens"] = cache_read_tokens
        att["cache_write_tokens"] = cache_write_tokens
        att["cost_usd"] = cost_usd
        att["duration_seconds"] = duration_seconds

    async def create_iteration(
        self,
        task_id: UUID,
        index: int,
        item_value: object,
    ) -> UUID:
        iteration_id = uuid6.uuid7()
        now = _now()
        self._iterations[iteration_id] = {
            "id": iteration_id,
            "task_id": task_id,
            "iteration_index": index,
            "item_value": json.dumps(item_value),
            "status": "running",
            "output": None,
            "structured_output": None,
            "check_results": None,
            "started_at": now,
            "finished_at": None,
        }
        return iteration_id

    async def complete_iteration(
        self,
        iteration_id: UUID,
        output: str | None,
        structured_output: dict[str, Any] | None,
        check_results: list[dict[str, Any]] | None,
    ) -> None:
        it = self._iterations[iteration_id]
        it["status"] = "completed"
        it["output"] = output
        it["structured_output"] = structured_output
        it["check_results"] = check_results
        it["finished_at"] = _now()

    async def fail_iteration(
        self,
        iteration_id: UUID,
        error: str,
    ) -> None:
        it = self._iterations[iteration_id]
        it["status"] = "failed"
        it["output"] = error
        it["finished_at"] = _now()

    # ── EventStorage ──

    async def write_event(
        self,
        run_id: UUID,
        event_type: str,
        data: dict[str, Any],
        task_id: UUID | None = None,
    ) -> UUID:
        event_id = uuid6.uuid7()
        self._events.append({
            "id": event_id,
            "run_id": run_id,
            "task_id": task_id,
            "event_type": event_type,
            "data": data,
            "created_at": _now(),
        })
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
        self._transcripts.append({
            "id": uuid6.uuid7(),
            "session_id": session_id,
            "run_id": run_id,
            "turn": turn,
            "role": role,
            "content": content,
            "tool_calls": tool_calls,
            "tokens": tokens,
            "created_at": _now(),
        })

    # ── RunStorage ──

    async def create_run(
        self, intent: str, config: dict[str, Any], autonomy_level: str,
    ) -> UUID:
        run_id = uuid6.uuid7()
        now = _now()
        self._runs[run_id] = {
            "id": run_id,
            "intent": intent,
            "config_snapshot": config,
            "autonomy_level": autonomy_level,
            "status": "created",
            "created_at": now,
            "finished_at": None,
            "coherence_summary": None,
            "total_input_tokens": 0,
            "total_output_tokens": 0,
            "total_reasoning_tokens": 0,
            "total_cache_read_tokens": 0,
            "total_cache_write_tokens": 0,
            "total_cost_usd": Decimal(0),
        }
        return run_id

    async def transition_run(
        self, run_id: UUID, new_status: str, reason: str | None = None,
    ) -> None:
        run = self._runs.get(run_id)
        if run is None:
            msg = f"run {run_id} not found"
            raise ValueError(msg)
        old_status: str = run["status"]
        validate_run_transition(old_status, new_status)
        run["status"] = new_status
        if new_status in ("completed", "failed", "aborted"):
            run["finished_at"] = _now()
        event_id = uuid6.uuid7()
        event_data = {
            "old_status": old_status,
            "new_status": new_status,
            "reason": reason,
        }
        self._events.append({
            "id": event_id,
            "run_id": run_id,
            "task_id": None,
            "event_type": "run_transition",
            "data": event_data,
            "created_at": _now(),
        })
        if self._event_callback is not None:
            await self._event_callback(event_id, run_id, "run_transition", event_data)
        if run_id in self._control_callbacks:
            await self._control_callbacks[run_id](run_id, new_status)

    async def write_coherence_summary(self, run_id: UUID, summary: str) -> None:
        run = self._runs.get(run_id)
        if run is not None:
            run["coherence_summary"] = summary

    # ── RunControlStorage ──

    async def subscribe_run_control(
        self, run_id: UUID, callback: Callable[[UUID, str], Awaitable[None]],
    ) -> None:
        self._control_callbacks[run_id] = callback
        run = self._runs.get(run_id)
        if run is not None and run["status"] in ("paused", "aborted"):
            await callback(run_id, run["status"])

    async def unsubscribe_run_control(self, run_id: UUID) -> None:
        self._control_callbacks.pop(run_id, None)

    # ── OverseerStorage ──

    async def write_decision(
        self,
        run_id: UUID,
        decision_type: str,
        choice: str,
        rationale: str | None = None,
    ) -> UUID:
        decision_id = uuid6.uuid7()
        self._decisions.append({
            "id": decision_id,
            "run_id": run_id,
            "decision_type": decision_type,
            "choice": choice,
            "rationale": rationale,
            "created_at": _now(),
        })
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
        self._constraints.append({
            "id": constraint_id,
            "run_id": run_id,
            "text": text,
            "tier": tier,
            "kind": kind,
            "args": args,
            "active": True,
            "created_at": _now(),
        })
        return constraint_id

    async def write_assumption(
        self,
        run_id: UUID,
        text: str,
        scope: str,
        inbox_item_id: UUID | None = None,
    ) -> UUID:
        assumption_id = uuid6.uuid7()
        self._assumptions.append({
            "id": assumption_id,
            "run_id": run_id,
            "text": text,
            "scope": scope,
            "status": "active",
            "inbox_item_id": inbox_item_id,
            "created_at": _now(),
        })
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
        self._lessons.append({
            "id": lesson_id,
            "run_id": run_id,
            "text": text,
            "relevance_tags": relevance_tags,
            "permanent": permanent,
            "source_files": source_files,
            "source_file": source_files[0] if source_files else None,
            "created_at": _now(),
        })
        return lesson_id

    async def update_workflow_status(
        self, workflow_id: UUID, current_step: str, health: str,
    ) -> None:
        self._workflow_status[workflow_id] = {
            "workflow_id": workflow_id,
            "current_step": current_step,
            "health": health,
            "updated_at": _now(),
        }

    async def write_context_diff(
        self, attempt_id: UUID, pre_refinement: str, refinement_diff: str,
    ) -> None:
        self._context_diffs.append({
            "id": uuid6.uuid7(),
            "attempt_id": attempt_id,
            "pre_refinement": pre_refinement,
            "refinement_diff": refinement_diff,
        })

    # ── InboxStorage ──

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
        now = _now()
        self._inbox_items[item_id] = {
            "id": item_id,
            "run_id": run_id,
            "status": "pending",
            "decision_type": decision_type,
            "question": question,
            "options": options,
            "assumed_option": assumed_option,
            "work_proceeding": work_proceeding,
            "contradiction_impact": contradiction_impact,
            "tags": tags or [],
            "deadline": deadline,
            "answer": None,
            "answer_event": answer_event,
            "rejection_reason": None,
            "answered_at": None,
            "created_at": now,
        }
        return item_id

    async def answer_inbox_item(self, item_id: UUID, answer: str) -> None:
        item = self._inbox_items.get(item_id)
        if item is None or item["status"] != "pending":
            current = item["status"] if item is not None else None
            msg = (
                f"cannot transition inbox item {item_id}"
                f" from {current!r} to 'answered':"
                " only 'pending' items can be answered"
            )
            raise InvalidTransitionError(msg)
        item["status"] = "answered"
        item["answer"] = answer
        item["answered_at"] = _now()

    async def skip_inbox_item(self, item_id: UUID) -> None:
        item = self._inbox_items.get(item_id)
        if item is None or item["status"] != "pending":
            current = item["status"] if item is not None else None
            msg = (
                f"cannot transition inbox item {item_id}"
                f" from {current!r} to 'skipped':"
                " only 'pending' items can be skipped"
            )
            raise InvalidTransitionError(msg)
        item["status"] = "skipped"

    async def reject_inbox_item(self, item_id: UUID, reason: str) -> None:
        item = self._inbox_items.get(item_id)
        if item is None or item["status"] != "pending":
            current = item["status"] if item is not None else None
            msg = (
                f"cannot transition inbox item {item_id}"
                f" from {current!r} to 'rejected':"
                " only 'pending' items can be rejected"
            )
            raise InvalidTransitionError(msg)
        item["status"] = "rejected"
        item["rejection_reason"] = reason

    async def expire_inbox_item(self, item_id: UUID) -> None:
        item = self._inbox_items.get(item_id)
        if item is None or item["status"] != "pending":
            current = item["status"] if item is not None else None
            msg = (
                f"cannot transition inbox item {item_id}"
                f" from {current!r} to 'expired':"
                " only 'pending' items can be expired"
            )
            raise InvalidTransitionError(msg)
        item["status"] = "expired"

    # ── NotepadStorage ──

    async def write_notepad_entry(
        self,
        run_id: UUID,
        task_name: str,
        agent_name: str,
        entry_type: str,
        text: str,
    ) -> None:
        self._notepad_entries.append({
            "id": uuid6.uuid7(),
            "run_id": run_id,
            "task_name": task_name,
            "agent_name": agent_name,
            "entry_type": entry_type,
            "text": text,
            "created_at": _now(),
        })

    # ── StorageReader ──

    async def list_tasks(self, run_id: UUID) -> list[TaskSummary]:
        results: list[TaskSummary] = []
        for task in self._tasks.values():
            if task["run_id"] != run_id:
                continue
            attempt_count = sum(
                1 for a in self._task_attempts.values()
                if a["task_id"] == task["id"]
            )
            results.append(TaskSummary(
                id=task["id"],
                name=task["name"],
                status=task["status"],
                task_type=task["task_type"],
                parent_task_id=task["parent_task_id"],
                attempt_count=attempt_count,
            ))
        # Sort by creation order (insertion order is preserved in dicts)
        return results

    async def read_task_attempt(
        self, task_id: UUID, attempt: int,
    ) -> TaskAttempt | None:
        for att in self._task_attempts.values():
            if att["task_id"] == task_id and att["attempt"] == attempt:
                return TaskAttempt.model_validate(att)
        return None

    async def read_latest_attempt(
        self, task_id: UUID,
    ) -> TaskAttempt | None:
        matching = [
            a for a in self._task_attempts.values()
            if a["task_id"] == task_id
        ]
        if not matching:
            return None
        best = max(matching, key=lambda a: a["attempt"])
        return TaskAttempt.model_validate(best)

    async def list_iterations(
        self, task_id: UUID,
    ) -> list[IterationResult]:
        matching = [
            it for it in self._iterations.values()
            if it["task_id"] == task_id
        ]
        matching.sort(key=lambda it: it["iteration_index"])
        return [IterationResult.model_validate(it) for it in matching]

    async def read_transcript(
        self, session_id: UUID,
    ) -> list[dict[str, Any]]:
        results = []
        for t in self._transcripts:
            if t["session_id"] == session_id:
                results.append({
                    "turn": t["turn"],
                    "role": t["role"],
                    "content": t["content"],
                    "tool_calls": t["tool_calls"],
                    "tokens": t["tokens"],
                    "created_at": t["created_at"],
                })
        return results

    async def search_transcript(
        self, session_id: UUID, query: str,
    ) -> list[dict[str, Any]]:
        results = []
        query_lower = query.lower()
        for t in self._transcripts:
            if t["session_id"] == session_id and query_lower in t["content"].lower():
                results.append({
                    "turn": t["turn"],
                    "role": t["role"],
                    "content": t["content"],
                    "tool_calls": t["tool_calls"],
                    "tokens": t["tokens"],
                    "created_at": t["created_at"],
                })
        return results

    async def read_run_report(
        self, run_id: UUID,
    ) -> RunReport | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        tasks = await self.list_tasks(run_id)
        decisions = [d for d in self._decisions if d["run_id"] == run_id]
        constraints = [c for c in self._constraints if c["run_id"] == run_id]
        assumptions = [a for a in self._assumptions if a["run_id"] == run_id]
        config_snapshot = run["config_snapshot"]
        if isinstance(config_snapshot, str):
            config_snapshot = json.loads(config_snapshot)
        return RunReport(
            id=run["id"],
            intent=run["intent"],
            status=run["status"],
            created_at=run["created_at"],
            finished_at=run["finished_at"],
            autonomy_level=run["autonomy_level"],
            config_snapshot=config_snapshot,
            total_input_tokens=run["total_input_tokens"],
            total_output_tokens=run["total_output_tokens"],
            total_reasoning_tokens=run["total_reasoning_tokens"],
            total_cache_read_tokens=run["total_cache_read_tokens"],
            total_cache_write_tokens=run["total_cache_write_tokens"],
            total_cost_usd=run["total_cost_usd"],
            coherence_summary=run["coherence_summary"],
            tasks=tasks,
            decisions=decisions,
            constraints=constraints,
            assumptions=assumptions,
        )

    async def list_runs(self) -> list[RunSummary]:
        results = []
        for run in reversed(list(self._runs.values())):
            results.append(RunSummary(
                id=run["id"],
                intent=run["intent"],
                status=run["status"],
                created_at=run["created_at"],
                finished_at=run["finished_at"],
            ))
        return results

    async def read_inbox(
        self, run_id: UUID, status: str | None = None,
    ) -> list[InboxItem]:
        results = []
        for item in self._inbox_items.values():
            if item["run_id"] != run_id:
                continue
            if status is not None and item["status"] != status:
                continue
            results.append(InboxItem.model_validate(item))
        results.sort(key=lambda i: i.created_at)
        return results

    async def read_notepad(
        self, run_id: UUID,
    ) -> list[NotepadEntry]:
        results = []
        for entry in self._notepad_entries:
            if entry["run_id"] == run_id:
                results.append(NotepadEntry(
                    run_id=entry["run_id"],
                    task_name=entry["task_name"],
                    agent_name=entry["agent_name"],
                    entry_type=entry["entry_type"],
                    text=entry["text"],
                    created_at=entry["created_at"],
                ))
        return results

    async def read_active_constraints(
        self, run_id: UUID,
    ) -> list[dict[str, Any]]:
        return [c for c in self._constraints if c["run_id"] == run_id]

    async def read_task_attempts(
        self, task_id: UUID,
    ) -> list[TaskAttempt]:
        matching = [
            a for a in self._task_attempts.values()
            if a["task_id"] == task_id
        ]
        matching.sort(key=lambda a: a["attempt"])
        return [TaskAttempt.model_validate(a) for a in matching]

    async def query_events(
        self,
        run_id: UUID,
        event_type: str | None = None,
        since: datetime | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        results = []
        for ev in self._events:
            if ev["run_id"] != run_id:
                continue
            if event_type is not None and ev["event_type"] != event_type:
                continue
            if since is not None and ev["created_at"] < since:
                continue
            results.append(ev)
            if len(results) >= limit:
                break
        return results

    async def read_inbox_item(
        self, item_id: UUID,
    ) -> InboxItem | None:
        item = self._inbox_items.get(item_id)
        if item is None:
            return None
        return InboxItem.model_validate(item)

    async def read_run_config(
        self, run_id: UUID,
    ) -> dict[str, Any] | None:
        run = self._runs.get(run_id)
        if run is None:
            return None
        config = run["config_snapshot"]
        if isinstance(config, str):
            result: dict[str, Any] = json.loads(config)
            return result
        return dict(config)

    async def read_session_token_counts(
        self, session_id: UUID,
    ) -> list[dict[str, Any]]:
        results = []
        for t in self._transcripts:
            if t["session_id"] == session_id and t["tokens"] is not None:
                results.append({"tokens": t["tokens"]})
        return results

    async def read_session_turn_count(
        self, session_id: UUID,
    ) -> int:
        return sum(1 for t in self._transcripts if t["session_id"] == session_id)

    async def query_relevant_lessons(
        self, tags: list[str],
    ) -> list[dict[str, Any]]:
        tag_set = set(tags)
        results = []
        for lesson in reversed(self._lessons):
            lesson_tags = lesson["relevance_tags"]
            if isinstance(lesson_tags, str):
                lesson_tags = json.loads(lesson_tags)
            if tag_set & set(lesson_tags):
                results.append({
                    "id": lesson["id"],
                    "text": lesson["text"],
                    "relevance_tags": lesson_tags,
                    "permanent": lesson["permanent"],
                    "source_file": lesson.get("source_file"),
                    "created_at": lesson["created_at"],
                })
        return results

    async def read_decisions(
        self, run_id: UUID, limit: int = 10,
    ) -> list[dict[str, Any]]:
        matching = [d for d in self._decisions if d["run_id"] == run_id]
        matching.reverse()
        return [
            {
                "id": d["id"],
                "decision_type": d["decision_type"],
                "choice": d["choice"],
                "rationale": d["rationale"],
                "created_at": d["created_at"],
            }
            for d in matching[:limit]
        ]

    async def read_constraints(
        self, run_id: UUID, active_only: bool = True,
    ) -> list[dict[str, Any]]:
        results = []
        for c in reversed(self._constraints):
            if c["run_id"] != run_id:
                continue
            if active_only and not c.get("active", True):
                continue
            results.append({
                "id": c["id"],
                "text": c["text"],
                "tier": c["tier"],
                "active": c.get("active", True),
                "created_at": c["created_at"],
            })
        return results

    async def read_assumptions(
        self, run_id: UUID, status: str | None = None,
    ) -> list[dict[str, Any]]:
        results = []
        for a in reversed(self._assumptions):
            if a["run_id"] != run_id:
                continue
            if status is not None and a.get("status") != status:
                continue
            results.append({
                "id": a["id"],
                "text": a["text"],
                "status": a.get("status", "active"),
                "scope": a["scope"],
                "inbox_item_id": a.get("inbox_item_id"),
                "created_at": a["created_at"],
            })
        return results

    async def query_lessons(
        self,
        run_id: UUID | None = None,
        tags: list[str] | None = None,
        permanent_only: bool = False,
    ) -> list[dict[str, Any]]:
        results = []
        tag_set = set(tags) if tags is not None else None
        for lesson in reversed(self._lessons):
            if run_id is not None and lesson["run_id"] != run_id:
                continue
            if permanent_only and not lesson["permanent"]:
                continue
            if tag_set is not None:
                lesson_tags = lesson["relevance_tags"]
                if isinstance(lesson_tags, str):
                    lesson_tags = json.loads(lesson_tags)
                if not (tag_set & set(lesson_tags)):
                    continue
            results.append({
                "id": lesson["id"],
                "text": lesson["text"],
                "relevance_tags": lesson["relevance_tags"],
                "permanent": lesson["permanent"],
                "source_file": lesson.get("source_file"),
                "created_at": lesson["created_at"],
            })
        return results

    async def read_workflow_status(
        self, workflow_id: UUID,
    ) -> dict[str, Any] | None:
        return self._workflow_status.get(workflow_id)

    # ── StorageLock ──

    async def acquire_run_lock(self, run_id: UUID) -> None:
        if self._run_locks.get(run_id):
            from orxtra.trace._lock import RunLockError  # noqa: PLC0415
            msg = f"run {run_id} is already locked by another process"
            raise RunLockError(msg)
        self._run_locks[run_id] = True

    async def release_run_lock(self, run_id: UUID) -> None:
        self._run_locks.pop(run_id, None)

    async def update_heartbeat(self, run_id: UUID) -> None:
        self._heartbeats[run_id] = time.monotonic()

    async def is_lock_stale(
        self, run_id: UUID, threshold_seconds: float = 300.0,
    ) -> bool:
        last = self._heartbeats.get(run_id)
        if last is None:
            return True
        return (time.monotonic() - last) > threshold_seconds

    # ── RecoveryOperations ──

    async def reclaim_interrupted(self) -> int:
        reclaimed = 0
        for task in list(self._tasks.values()):
            if task["status"] in ("active", "prechecking", "postchecking"):
                task["status"] = "cancelled"
                self._events.append({
                    "id": uuid6.uuid7(),
                    "run_id": task["run_id"],
                    "task_id": task["id"],
                    "event_type": "crash_recovery",
                    "data": {"action": "reclaim_interrupted"},
                    "created_at": _now(),
                })
                reclaimed += 1
        return reclaimed

    async def reevaluate_blocked(self) -> list[UUID]:
        results: list[UUID] = []
        for task in self._tasks.values():
            if task["status"] != "created":
                continue
            parent_id = task["parent_task_id"]
            if parent_id is None:
                results.append(task["id"])
                continue
            parent = self._tasks.get(parent_id)
            if parent is not None and parent["status"] == "completed":
                results.append(task["id"])
        return results

    async def clean_orphaned(self) -> int:
        cleaned = 0
        for run in list(self._runs.values()):
            if run["status"] not in ("running", "paused"):
                continue
            run_id = run["id"]
            if not self._run_locks.get(run_id):
                # No lock held means the process crashed
                run["status"] = "failed"
                run["finished_at"] = _now()
                self._events.append({
                    "id": uuid6.uuid7(),
                    "run_id": run_id,
                    "task_id": None,
                    "event_type": "crash_recovery",
                    "data": {"action": "clean_orphaned"},
                    "created_at": _now(),
                })
                cleaned += 1
        return cleaned


class InMemoryEventBus:
    """In-memory implementation of EventBus for tests and lightweight use."""

    def __init__(self) -> None:
        self._subscribers: dict[str, list[Callable[[str], Awaitable[None]]]] = {}

    async def subscribe(
        self, channel: str, callback: Callable[[str], Awaitable[None]],
    ) -> None:
        self._subscribers.setdefault(channel, []).append(callback)

    async def publish(self, channel: str, payload: str) -> None:
        for callback in self._subscribers.get(channel, []):
            await callback(payload)


def _now() -> datetime:
    """Return current UTC datetime."""
    from datetime import datetime, timezone  # noqa: PLC0415
    return datetime.now(timezone.utc)
