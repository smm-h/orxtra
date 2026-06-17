from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxt.protocols._task import TaskSpec
from orxt.scheduler._executor import Scheduler

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    MockTransportNoTools,
    make_agent,
    make_categories,
)

if TYPE_CHECKING:
    from uuid import UUID

    from orxt.scheduler._overseer import OverseerEvent


class MockOverseerInterface:
    def __init__(self) -> None:
        self.events_sent: list[OverseerEvent] = []

    async def send_event(self, event: OverseerEvent) -> None:
        self.events_sent.append(event)

    async def verify_actions(
        self, event_type: str = "",
    ) -> list[str]:
        return []

    async def send_correction(
        self, message: str,
    ) -> None:
        pass

    def is_degraded(
        self, event_type: str,
    ) -> bool:
        return False

    async def refine_context(
        self, task_name: str, raw_context: str,
    ) -> str:
        return raw_context


def _make_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: UUID,
    read_root: Path,
    overseer: MockOverseerInterface | None = None,
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
        overseer_interface=overseer,
    )


@pytest.fixture
def run_id() -> UUID:
    return uuid6.uuid7()


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.mark.asyncio
async def test_escalated_task_sends_event(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """Escalated task sends TaskEscalated event to Overseer."""
    mock_overseer = MockOverseerInterface()
    # Use a transport that never calls start_task/end_task
    # so the agent session ends without completing
    bad_transport = MockTransportNoTools()
    scheduler = Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": bad_transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=tmp_path,
        overseer_interface=mock_overseer,
    )

    task = TaskSpec(
        name="failing_task",
        agent="test-agent",
        task_prompt="Do something",
        retry=0,
    )
    await scheduler.execute_task(
        task, None,
    )
    # The task should have escalated
    # Check that an event was sent
    escalation_events = [
        e for e in mock_overseer.events_sent
        if type(e).__name__ == "TaskEscalated"
    ]
    assert len(escalation_events) >= 1


@pytest.mark.asyncio
async def test_escalation_payload_content(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """Escalation payload includes task name and attempts."""
    mock_overseer = MockOverseerInterface()
    bad_transport = MockTransportNoTools()
    scheduler = Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": bad_transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=tmp_path,
        overseer_interface=mock_overseer,
    )

    task = TaskSpec(
        name="failing_task",
        agent="test-agent",
        task_prompt="Do something",
        retry=1,
    )
    await scheduler.execute_task(task, None)
    escalation_events = [
        e for e in mock_overseer.events_sent
        if type(e).__name__ == "TaskEscalated"
    ]
    if escalation_events:
        event = escalation_events[0]
        assert event.task_name == "failing_task"
        assert event.payload.attempts == 2


@pytest.mark.asyncio
async def test_no_overseer_escalation_noop(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """No Overseer: escalation event is no-op."""
    bad_transport = MockTransportNoTools()
    scheduler = Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": bad_transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=tmp_path,
        # No overseer_interface
    )

    task = TaskSpec(
        name="failing_task",
        agent="test-agent",
        task_prompt="Do something",
        retry=0,
    )
    # Should not raise, just completes
    result = await scheduler.execute_task(
        task, None,
    )
    assert result is not None


@pytest.mark.asyncio
async def test_decision_point_sends_event(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """Decision point task sends event to Overseer."""
    mock_overseer = MockOverseerInterface()
    scheduler = _make_scheduler(
        trace_writer, transport, run_id,
        read_root=tmp_path,
        overseer=mock_overseer,
    )

    task = TaskSpec(
        name="decide_approach",
        decision_point=True,
    )
    result = await scheduler.execute_task(
        task, None,
    )
    assert result.check_results[0].passed
    # Check that an advisory event was sent
    advisory_events = [
        e for e in mock_overseer.events_sent
        if type(e).__name__ == "StructuralAdvisory"
    ]
    assert len(advisory_events) >= 1
    assert (
        "decide_approach"
        in advisory_events[0].observation
    )


@pytest.mark.asyncio
async def test_decision_point_no_overseer(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """Decision point without Overseer still completes."""
    scheduler = _make_scheduler(
        trace_writer, transport, run_id,
        read_root=tmp_path,
    )

    task = TaskSpec(
        name="decide_approach",
        decision_point=True,
    )
    result = await scheduler.execute_task(
        task, None,
    )
    assert result.check_results[0].passed
    assert result.check_results[0].message == (
        "Decision point completed"
    )
