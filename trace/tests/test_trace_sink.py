"""Tests for TraceSink -- EventSink[OverseerEvent] implementation."""

from __future__ import annotations

from decimal import Decimal
from typing import Any
from uuid import UUID

import pytest
import uuid6
from orxtra.protocols import (
    BudgetExhausted,
    BudgetThresholdCrossed,
    EscalationPayload,
    RunStarted,
    StructuralAdvisory,
    TaskContext,
    TaskEscalated,
)
from orxtra.trace._trace_sink import TraceSink, _serialize_event, _to_snake_case

# ── Unit tests for helpers ──


class TestToSnakeCase:
    def test_simple(self) -> None:
        assert _to_snake_case("RunStarted") == "run_started"

    def test_consecutive_caps(self) -> None:
        assert _to_snake_case("BudgetExhausted") == "budget_exhausted"

    def test_threshold_crossed(self) -> None:
        assert _to_snake_case("BudgetThresholdCrossed") == "budget_threshold_crossed"

    def test_task_escalated(self) -> None:
        assert _to_snake_case("TaskEscalated") == "task_escalated"

    def test_structural_advisory(self) -> None:
        assert _to_snake_case("StructuralAdvisory") == "structural_advisory"

    def test_single_word(self) -> None:
        assert _to_snake_case("Event") == "event"


class TestSerializeEvent:
    def test_run_started(self) -> None:
        event = RunStarted(intent="test", config_snapshot={"key": "val"})
        result = _serialize_event(event)
        assert result["intent"] == "test"
        assert result["config_snapshot"] == {"key": "val"}

    def test_uuid_serialized_as_string(self) -> None:
        task_id = uuid6.uuid7()
        event = BudgetExhausted(workflow_id=task_id)
        result = _serialize_event(event)
        assert result["workflow_id"] == str(task_id)

    def test_decimal_serialized_as_string(self) -> None:
        task_id = uuid6.uuid7()
        event = BudgetThresholdCrossed(
            workflow_id=task_id,
            budget_usd=Decimal("100.50"),
            spent_usd=Decimal("80.00"),
            threshold_pct=0.8,
        )
        result = _serialize_event(event)
        assert result["budget_usd"] == str(Decimal("100.50"))
        assert result["spent_usd"] == str(Decimal("80.00"))
        assert result["threshold_pct"] == 0.8

    def test_structural_advisory(self) -> None:
        task_id = uuid6.uuid7()
        event = StructuralAdvisory(
            task_id=task_id,
            observation="test observation",
            suggestion="test suggestion",
        )
        result = _serialize_event(event)
        assert result["task_id"] == str(task_id)
        assert result["observation"] == "test observation"
        assert result["suggestion"] == "test suggestion"


# ── MockTraceWriter for TraceSink tests ──


class MockTraceWriter:
    """Minimal mock that records write_event calls."""

    def __init__(self) -> None:
        self.events: list[dict[str, Any]] = []

    async def write_event(
        self,
        run_id: UUID | None,
        event_type: str,
        data: dict[str, Any],
        task_id: UUID | None = None,
        *,
        principal_id: UUID,
        idempotency_key: str | None = None,
    ) -> tuple[UUID, bool]:
        event_id = uuid6.uuid7()
        self.events.append({
            "event_id": event_id,
            "run_id": run_id,
            "event_type": event_type,
            "data": data,
            "principal_id": principal_id,
        })
        return event_id, True


# ── Integration tests ──


class TestTraceSink:
    @pytest.fixture
    def run_id(self) -> UUID:
        return uuid6.uuid7()

    @pytest.fixture
    def run_principal_id(self) -> UUID:
        return uuid6.uuid7()

    @pytest.fixture
    def writer(self) -> MockTraceWriter:
        return MockTraceWriter()

    @pytest.fixture
    def sink(
        self, writer: MockTraceWriter, run_id: UUID, run_principal_id: UUID,
    ) -> TraceSink:
        return TraceSink(
            trace_writer=writer,  # type: ignore[arg-type]
            run_id=run_id,
            run_principal_id=run_principal_id,
        )

    async def test_run_started(
        self, sink: TraceSink, writer: MockTraceWriter, run_id: UUID,
        run_principal_id: UUID,
    ) -> None:
        event = RunStarted(
            intent="deploy app",
            config_snapshot={"name": "test_workflow"},
        )
        await sink.on_event(event)

        assert len(writer.events) == 1
        written = writer.events[0]
        assert written["run_id"] == run_id
        assert written["event_type"] == "run_started"
        assert written["principal_id"] == run_principal_id
        assert written["data"]["intent"] == "deploy app"
        assert written["data"]["config_snapshot"] == {"name": "test_workflow"}

    async def test_task_escalated(
        self, sink: TraceSink, writer: MockTraceWriter,
    ) -> None:
        task_id = uuid6.uuid7()
        context = TaskContext(
            variables={},
            run_id=uuid6.uuid7(),
            task_name="failing_task",
            task_id=task_id,
            attempt=3,
            prior_attempts=None,
            notepad_content="",
            parent_task_id=None,
            nesting_depth=0,
        )
        payload = EscalationPayload(
            task_name="failing_task",
            task_id=task_id,
            agent_name="coder",
            attempts=3,
            failed_checks=[],
            agent_summary="Retries exhausted",
            context=context,
        )
        event = TaskEscalated(
            task_id=task_id,
            task_name="failing_task",
            from_child_task_id=task_id,
            payload=payload,
        )
        await sink.on_event(event)

        assert len(writer.events) == 1
        written = writer.events[0]
        assert written["event_type"] == "task_escalated"
        assert written["data"]["task_name"] == "failing_task"

    async def test_budget_threshold_crossed(
        self, sink: TraceSink, writer: MockTraceWriter,
        run_principal_id: UUID,
    ) -> None:
        wf_id = uuid6.uuid7()
        event = BudgetThresholdCrossed(
            workflow_id=wf_id,
            budget_usd=Decimal("10.00"),
            spent_usd=Decimal("8.50"),
            threshold_pct=0.8,
        )
        await sink.on_event(event)

        assert len(writer.events) == 1
        written = writer.events[0]
        assert written["event_type"] == "budget_threshold_crossed"
        assert written["principal_id"] == run_principal_id

    async def test_budget_exhausted(
        self, sink: TraceSink, writer: MockTraceWriter,
    ) -> None:
        wf_id = uuid6.uuid7()
        event = BudgetExhausted(workflow_id=wf_id)
        await sink.on_event(event)

        assert len(writer.events) == 1
        written = writer.events[0]
        assert written["event_type"] == "budget_exhausted"

    async def test_multiple_events(
        self, sink: TraceSink, writer: MockTraceWriter,
    ) -> None:
        """TraceSink handles multiple events in sequence."""
        event1 = RunStarted(intent="first", config_snapshot={})
        event2 = RunStarted(intent="second", config_snapshot={})
        await sink.on_event(event1)
        await sink.on_event(event2)

        assert len(writer.events) == 2
        assert writer.events[0]["data"]["intent"] == "first"
        assert writer.events[1]["data"]["intent"] == "second"
