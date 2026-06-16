from __future__ import annotations

from decimal import Decimal

import pytest
import uuid6
from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)
from orxt.protocols._task import TaskSpec
from orxt.scheduler._executor import Scheduler
from orxt.transport import Usage


@pytest.fixture
def run_id():
    return uuid6.uuid7()


@pytest.fixture
def trace_writer():
    return MockTraceWriter()


@pytest.fixture
def transport():
    return MockTransport()


def _make_scheduler(
    trace_writer,
    transport,
    run_id,
    budget_exhaustion_policy="unlimited",
    overseer_interface=None,
):
    return Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        budget_exhaustion_policy=(
            budget_exhaustion_policy
        ),
        overseer_interface=overseer_interface,
    )


class MockOverseerInterface:
    def __init__(self):
        self.events_sent = []

    async def send_event(self, event):
        self.events_sent.append(event)

    async def verify_actions(
        self, event_type="",
    ):
        return []

    async def send_correction(self, message):
        pass

    def is_degraded(self, event_type):
        return False

    async def refine_context(
        self, task_name, raw_context,
    ):
        return raw_context


def test_cost_tracking_accumulates(
    trace_writer, transport, run_id,
):
    """Cost tracking accumulates correctly."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
        budget=Decimal("10.0"),
    )
    scheduler._task_specs[task_id] = task
    scheduler._task_costs[task_id] = Decimal(0)
    scheduler._accumulate_cost(
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
    assert scheduler._task_costs[task_id] > Decimal(0)


def test_no_budget_no_enforcement(
    trace_writer, transport, run_id,
):
    """No budget set: no enforcement events."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
    )
    scheduler._task_specs[task_id] = task
    scheduler._task_costs[task_id] = Decimal(0)
    scheduler._accumulate_cost(
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
        len(scheduler._budget_threshold_events) == 0
    )
    assert (
        len(scheduler._budget_exhausted_events) == 0
    )


def test_budget_threshold_event_at_80_pct(
    trace_writer, transport, run_id,
):
    """Budget threshold event emitted at 80%."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
        budget=Decimal("0.001"),
    )
    scheduler._task_specs[task_id] = task
    scheduler._task_costs[task_id] = Decimal(0)
    scheduler._accumulate_cost(
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
        len(scheduler._budget_threshold_events) >= 1
    )
    assert (
        scheduler._budget_threshold_events[0][0]
        == task_id
    )


def test_budget_exhausted_event(
    trace_writer, transport, run_id,
):
    """Budget exhausted event when cost >= budget."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id,
    )
    task_id = uuid6.uuid7()
    task = TaskSpec(
        name="test",
        agent="test-agent",
        task_prompt="do stuff",
        budget=Decimal("0.001"),
    )
    scheduler._task_specs[task_id] = task
    scheduler._task_costs[task_id] = Decimal(0)
    scheduler._accumulate_cost(
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
        len(scheduler._budget_exhausted_events) >= 1
    )


@pytest.mark.asyncio
async def test_budget_events_sent_to_overseer(
    trace_writer, transport, run_id,
):
    """Budget events are sent to overseer."""
    mock_overseer = MockOverseerInterface()
    scheduler = _make_scheduler(
        trace_writer,
        transport,
        run_id,
        overseer_interface=mock_overseer,
    )
    task_id = uuid6.uuid7()
    scheduler._budget_threshold_events.append(
        (
            task_id,
            "test",
            Decimal("1.0"),
            Decimal("0.9"),
        ),
    )
    await scheduler._send_budget_events(task_id)
    assert len(mock_overseer.events_sent) >= 1


def test_unlimited_policy_no_enforcement(
    trace_writer, transport, run_id,
):
    """Unlimited policy does nothing special."""
    scheduler = _make_scheduler(
        trace_writer,
        transport,
        run_id,
        budget_exhaustion_policy="unlimited",
    )
    assert (
        scheduler._budget_exhaustion_policy
        == "unlimited"
    )
