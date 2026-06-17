from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxt.protocols._task import BudgetExhaustionPolicy, TaskSpec
from orxt.scheduler._executor import Scheduler
from orxt.transport import Usage

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)

if TYPE_CHECKING:
    from pathlib import Path

    from orxt.scheduler._overseer import OverseerEvent


@pytest.fixture
def run_id() -> uuid6.UUID:
    return uuid6.uuid7()


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


def _make_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: uuid6.UUID,
    read_root: Path,
    budget_exhaustion_policy: BudgetExhaustionPolicy = BudgetExhaustionPolicy.UNLIMITED,
    overseer_interface: MockOverseerInterface | None = None,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=read_root,
        budget_exhaustion_policy=(
            budget_exhaustion_policy
        ),
        overseer_interface=overseer_interface,
    )


class MockOverseerInterface:
    def __init__(self) -> None:
        self.events_sent: list[OverseerEvent] = []

    async def send_event(self, event: OverseerEvent) -> None:
        self.events_sent.append(event)

    async def verify_actions(
        self, event_type: str = "",
    ) -> list[str]:
        return []

    async def send_correction(self, message: str) -> None:
        pass

    def is_degraded(self, event_type: str) -> bool:
        return False

    async def refine_context(
        self, task_name: str, raw_context: str,
    ) -> str:
        return raw_context


def test_cost_tracking_accumulates(
    trace_writer: MockTraceWriter, transport: MockTransport, run_id: uuid6.UUID, tmp_path: Path,
) -> None:
    """Cost tracking accumulates correctly."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id, read_root=tmp_path,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
        budget=Decimal("10.0"),
    )
    scheduler._task_specs[task_id] = task  # noqa: SLF001
    scheduler._task_costs[task_id] = Decimal(0)  # noqa: SLF001
    scheduler._accumulate_cost(  # noqa: SLF001
        task_id,
        task,
        Usage(
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
    )
    assert scheduler._task_costs[task_id] > Decimal(0)  # noqa: SLF001


def test_no_budget_no_enforcement(
    trace_writer: MockTraceWriter, transport: MockTransport, run_id: uuid6.UUID, tmp_path: Path,
) -> None:
    """No budget set: no enforcement events."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id, read_root=tmp_path,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
    )
    scheduler._task_specs[task_id] = task  # noqa: SLF001
    scheduler._task_costs[task_id] = Decimal(0)  # noqa: SLF001
    scheduler._accumulate_cost(  # noqa: SLF001
        task_id,
        task,
        Usage(
            input_tokens=1000,
            output_tokens=500,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
    )
    assert (
        len(scheduler._budget_threshold_events) == 0  # noqa: SLF001
    )
    assert (
        len(scheduler._budget_exhausted_events) == 0  # noqa: SLF001
    )


def test_budget_threshold_event_at_80_pct(
    trace_writer: MockTraceWriter, transport: MockTransport, run_id: uuid6.UUID, tmp_path: Path,
) -> None:
    """Budget threshold event emitted at 80%."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id, read_root=tmp_path,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
        budget=Decimal("0.001"),
    )
    scheduler._task_specs[task_id] = task  # noqa: SLF001
    scheduler._task_costs[task_id] = Decimal(0)  # noqa: SLF001
    scheduler._accumulate_cost(  # noqa: SLF001
        task_id,
        task,
        Usage(
            input_tokens=100000,
            output_tokens=50000,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
    )
    assert (
        len(scheduler._budget_threshold_events) >= 1  # noqa: SLF001
    )
    assert (
        scheduler._budget_threshold_events[0][0]  # noqa: SLF001
        == task_id
    )


def test_budget_exhausted_event(
    trace_writer: MockTraceWriter, transport: MockTransport, run_id: uuid6.UUID, tmp_path: Path,
) -> None:
    """Budget exhausted event when cost >= budget."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id, read_root=tmp_path,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
        budget=Decimal("0.001"),
    )
    scheduler._task_specs[task_id] = task  # noqa: SLF001
    scheduler._task_costs[task_id] = Decimal(0)  # noqa: SLF001
    scheduler._accumulate_cost(  # noqa: SLF001
        task_id,
        task,
        Usage(
            input_tokens=100000,
            output_tokens=50000,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        ),
    )
    assert (
        len(scheduler._budget_exhausted_events) >= 1  # noqa: SLF001
    )


@pytest.mark.asyncio
async def test_budget_events_sent_to_overseer(
    trace_writer: MockTraceWriter, transport: MockTransport, run_id: uuid6.UUID, tmp_path: Path,
) -> None:
    """Budget events are sent to overseer."""
    mock_overseer = MockOverseerInterface()
    scheduler = _make_scheduler(
        trace_writer,
        transport,
        run_id,
        read_root=tmp_path,
        overseer_interface=mock_overseer,
    )
    task_id = uuid6.uuid7()
    scheduler._budget_threshold_events.append(  # noqa: SLF001
        (
            task_id,
            "test",
            Decimal("1.0"),
            Decimal("0.9"),
        ),
    )
    await scheduler._send_budget_events(task_id)  # noqa: SLF001
    assert len(mock_overseer.events_sent) >= 1


def test_unlimited_policy_no_enforcement(
    trace_writer: MockTraceWriter, transport: MockTransport, run_id: uuid6.UUID, tmp_path: Path,
) -> None:
    """Unlimited policy does nothing special."""
    scheduler = _make_scheduler(
        trace_writer,
        transport,
        run_id,
        read_root=tmp_path,
        budget_exhaustion_policy=BudgetExhaustionPolicy.UNLIMITED,
    )
    assert (
        scheduler._budget_exhaustion_policy  # noqa: SLF001
        == BudgetExhaustionPolicy.UNLIMITED
    )
