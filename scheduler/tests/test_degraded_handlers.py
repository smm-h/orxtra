"""Tests for degraded mode handler functions.

Covers the three fallback handlers used when the Overseer
is degraded, and the FALLBACK_HANDLERS dispatch table.
"""

from __future__ import annotations

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

import uuid6

if TYPE_CHECKING:
    import pytest
from orxtra.protocols._events import (
    BudgetThresholdCrossed,
    HealthDegraded,
    TaskFailed,
)
from orxtra.protocols._task import EscalationPayload, TaskContext
from orxtra.scheduler._overseer import (
    FALLBACK_HANDLERS,
    _escalate_to_human_inbox,
    _fixed_escalation_ladder,
    _log_only,
    _maintain_current_allocations,
    _write_to_trace,
)

from tests.conftest import MockTraceWriter


def _make_task_failed() -> TaskFailed:
    tid = uuid6.uuid7()
    rid = uuid6.uuid7()
    return TaskFailed(
        task_id=tid,
        task_name="test-task",
        payload=EscalationPayload(
            task_name="test-task",
            task_id=tid,
            agent_name="agent",
            attempts=1,
            failed_checks=[],
            agent_summary="failed",
            context=TaskContext(
                variables={},
                run_id=rid,
                task_name="test-task",
                task_id=tid,
                attempt=1,
                prior_attempts=None,
                notepad_content="",
                parent_task_id=None,
                nesting_depth=0,
            ),
        ),
    )


def _make_budget_threshold_crossed() -> BudgetThresholdCrossed:
    return BudgetThresholdCrossed(
        workflow_id=uuid6.uuid7(),
        budget_usd=Decimal("10.00"),
        spent_usd=Decimal("8.50"),
        threshold_pct=0.85,
    )


def _make_health_degraded() -> HealthDegraded:
    return HealthDegraded(
        event_type="TaskFailed",
        failure_rate=0.6,
        threshold=0.5,
    )


class _MockTraceWriter:
    """Minimal mock that only records create_inbox_item calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def create_inbox_item(self, **kwargs: Any) -> Any:  # noqa: ANN401
        self.calls.append(("create_inbox_item", kwargs))
        return uuid6.uuid7()


# -- Tests -------------------------------------------------


async def test_fixed_escalation_ladder_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_fixed_escalation_ladder logs with the event type name."""
    logger = logging.getLogger("test.degraded")
    event = _make_task_failed()
    with caplog.at_level(logging.INFO, logger="test.degraded"):
        await _fixed_escalation_ladder(event, logger)
    assert any(
        "fixed escalation" in r.message and "TaskFailed" in r.message
        for r in caplog.records
    )


async def test_maintain_current_allocations_is_noop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_maintain_current_allocations logs but does not call trace_writer."""
    logger = logging.getLogger("test.degraded")
    event = _make_budget_threshold_crossed()
    tw = _MockTraceWriter()
    with caplog.at_level(logging.INFO, logger="test.degraded"):
        await _maintain_current_allocations(
            event, logger, trace_writer=tw, run_id=uuid6.uuid7(),
        )
    assert any(
        "maintaining current" in r.message
        and "BudgetThresholdCrossed" in r.message
        for r in caplog.records
    )
    assert tw.calls == []


async def test_escalate_to_human_inbox_creates_inbox_item() -> None:
    """_escalate_to_human_inbox calls trace_writer.create_inbox_item
    when both trace_writer and run_id are provided."""
    logger = logging.getLogger("test.degraded")
    event = _make_health_degraded()
    tw = _MockTraceWriter()
    run_id = uuid6.uuid7()
    await _escalate_to_human_inbox(
        event, logger, trace_writer=tw, run_id=run_id,
    )
    assert len(tw.calls) == 1
    method, kwargs = tw.calls[0]
    assert method == "create_inbox_item"
    assert kwargs["run_id"] is run_id
    assert kwargs["decision_type"] == "degraded_escalation"
    assert "HealthDegraded" in kwargs["question"]


async def test_escalate_to_human_inbox_skips_without_trace_writer(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """_escalate_to_human_inbox only logs when trace_writer is None."""
    logger = logging.getLogger("test.degraded")
    event = _make_health_degraded()
    with caplog.at_level(logging.INFO, logger="test.degraded"):
        await _escalate_to_human_inbox(event, logger)
    assert any(
        "escalating" in r.message and "HealthDegraded" in r.message
        for r in caplog.records
    )


async def test_escalate_to_human_inbox_skips_without_run_id() -> None:
    """_escalate_to_human_inbox does not call trace_writer
    when run_id is None."""
    logger = logging.getLogger("test.degraded")
    event = _make_health_degraded()
    tw = _MockTraceWriter()
    await _escalate_to_human_inbox(
        event, logger, trace_writer=tw, run_id=None,
    )
    assert tw.calls == []


async def test_fallback_handlers_dispatch_correctly() -> None:
    """FALLBACK_HANDLERS maps string names to the correct functions."""
    assert (
        FALLBACK_HANDLERS["fixed_escalation_ladder"]
        is _fixed_escalation_ladder
    )
    assert (
        FALLBACK_HANDLERS["maintain_current_allocations"]
        is _maintain_current_allocations
    )
    assert (
        FALLBACK_HANDLERS["escalate_to_human_inbox"]
        is _escalate_to_human_inbox
    )
    assert (
        FALLBACK_HANDLERS["write_to_trace"]
        is _write_to_trace
    )
    assert (
        FALLBACK_HANDLERS["log_only"]
        is _log_only
    )
    assert len(FALLBACK_HANDLERS) == 5


async def test_escalate_with_conftest_mock_trace_writer() -> None:
    """Verify _escalate_to_human_inbox works with the shared
    MockTraceWriter from conftest."""
    logger = logging.getLogger("test.degraded")
    event = _make_health_degraded()
    tw = MockTraceWriter()
    run_id = uuid6.uuid7()
    await _escalate_to_human_inbox(
        event, logger, trace_writer=tw, run_id=run_id,
    )
    inbox_calls = tw.get_calls("create_inbox_item")
    assert len(inbox_calls) == 1
    assert inbox_calls[0]["decision_type"] == "degraded_escalation"
