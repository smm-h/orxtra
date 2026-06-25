from __future__ import annotations

from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

import pytest
import uuid6

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from uuid import UUID


class MockTraceWriter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._next_id: UUID | None = None

    def set_next_id(self, next_id: UUID) -> None:
        self._next_id = next_id

    def _gen_id(self) -> UUID:
        if self._next_id is not None:
            result = self._next_id
            self._next_id = None
            return result
        return uuid6.uuid7()

    async def write_decision(
        self,
        run_id: UUID,
        decision_type: str,
        choice: str,
        rationale: str | None = None,
    ) -> UUID:
        generated = self._gen_id()
        self.calls.append(("write_decision", {
            "run_id": run_id, "decision_type": decision_type,
            "choice": choice, "rationale": rationale,
        }))
        return generated

    async def write_constraint(
        self,
        run_id: UUID,
        text: str,
        tier: str,
        kind: str,
        args: dict[str, Any] | None = None,
    ) -> UUID:
        generated = self._gen_id()
        self.calls.append(("write_constraint", {
            "run_id": run_id, "text": text, "tier": tier,
            "kind": kind, "args": args,
        }))
        return generated

    async def write_assumption(
        self,
        run_id: UUID,
        text: str,
        scope: str,
        inbox_item_id: UUID | None = None,
    ) -> UUID:
        generated = self._gen_id()
        self.calls.append(("write_assumption", {
            "run_id": run_id, "text": text, "scope": scope,
            "inbox_item_id": inbox_item_id,
        }))
        return generated

    async def create_inbox_item(  # noqa: PLR0913
        self,
        run_id: UUID,
        decision_type: str,
        question: str,
        options: list[dict[str, Any]],
        assumed_option: str | None = None,
        work_proceeding: str | None = None,
        contradiction_impact: str | None = None,
        tags: list[str] | None = None,
        deadline: Any = None,  # noqa: ANN401
        answer_event: str | None = None,
    ) -> UUID:
        generated = self._gen_id()
        self.calls.append(("create_inbox_item", {
            "run_id": run_id, "decision_type": decision_type,
            "question": question, "options": options,
            "assumed_option": assumed_option,
            "work_proceeding": work_proceeding,
            "contradiction_impact": contradiction_impact,
            "tags": tags, "deadline": deadline,
            "answer_event": answer_event,
        }))
        return generated

    async def write_lesson(
        self,
        run_id: UUID,
        text: str,
        relevance_tags: list[str],
        permanent: bool,
        source_files: list[str] | None = None,
    ) -> UUID:
        generated = self._gen_id()
        self.calls.append(("write_lesson", {
            "run_id": run_id, "text": text,
            "relevance_tags": relevance_tags,
            "permanent": permanent, "source_files": source_files,
        }))
        return generated

    async def update_workflow_status(
        self, workflow_id: UUID, current_step: str, health: str,
    ) -> None:
        self.calls.append(("update_workflow_status", {
            "workflow_id": workflow_id, "current_step": current_step,
            "health": health,
        }))

    async def write_event(
        self,
        run_id: UUID | None,
        event_type: str,
        data: dict[str, Any],
        task_id: UUID | None = None,
        source: str = "internal",
    ) -> UUID:
        generated = self._gen_id()
        self.calls.append(("write_event", {
            "run_id": run_id, "event_type": event_type,
            "data": data, "task_id": task_id,
        }))
        return generated


class MockConn:
    def __init__(
        self, rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._rows = rows or []

    async def fetch(
        self, query: str, *args: Any,  # noqa: ANN401
    ) -> list[dict[str, Any]]:
        return self._rows

    async def fetchrow(
        self, query: str, *args: Any,  # noqa: ANN401
    ) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class MockPool:
    def __init__(
        self, rows: list[dict[str, Any]] | None = None,
    ) -> None:
        self._conn = MockConn(rows)

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[MockConn]:
        yield self._conn


class MockSession:
    def __init__(self) -> None:
        self.sent_messages: list[str] = []
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0
        self.turn_count: int = 0

    @property
    def model(self) -> str:
        return "test-model"

    @property
    def system_prompt(self) -> str:
        return "test-prompt"

    @property
    def tools(self) -> list[Any]:
        return []

    @property
    def session_id(self) -> str | None:
        return "test-session-id"

    async def send(
        self, message: str,
    ) -> AsyncIterator[Any]:
        self.sent_messages.append(message)
        return
        yield

    def resume_id(self) -> str:
        return "test-session-id"


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def run_id() -> UUID:
    return uuid6.uuid7()


@pytest.fixture
def mock_pool() -> MockPool:
    return MockPool()
