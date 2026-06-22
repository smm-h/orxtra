from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.protocols._task import TaskSpec, TaskState
from orxtra.protocols._tools import CreateWorkflowParams
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._services import ServiceInstance
from orxtra.scheduler._types import (
    EscalationPolicy,
    ServiceConfig,
    WorkflowConfig,
)

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
)

if TYPE_CHECKING:
    from pathlib import Path

    from orxtra.scheduler._overseer import OverseerEvent


# -- Helpers ---------------------------------------------------------


class MockOverseerInterface:
    """Minimal Overseer mock for testing escalation routing."""

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


class MockParentSession:
    """Fake session that records messages sent to it."""

    def __init__(self) -> None:
        self.messages: list[str] = []
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0
        self.total_reasoning_tokens: int = 0
        self.total_cache_read_tokens: int = 0
        self.total_cache_write_tokens: int = 0
        self.session_id: str = "mock-parent-session"

    async def send(self, message: str):  # noqa: ANN201
        """Async generator matching Session.send interface."""
        self.messages.append(message)
        return
        yield


def _make_mixed_scheduler(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    read_root: Path,
    overseer: MockOverseerInterface | None = None,
) -> Scheduler:
    """Scheduler with two providers: bad (no tools) and good (tools).

    Agents using category "bad" will escalate because the
    transport never calls start_task/end_task.
    Agents using category "good" will complete normally.
    """
    bad_transport = MockTransport()
    good_transport = MockTransport(auto_execute_tools=True)
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={
            "google": bad_transport,
            "openai": good_transport,
        },
        agents={
            "bad-agent": Agent(
                name="bad-agent",
                description="Agent that escalates",
                prompt="You are a test agent.",
                category="bad",
                allow=["read"],
            ),
            "good-agent": Agent(
                name="good-agent",
                description="Agent that completes",
                prompt="You are a test agent.",
                category="good",
                allow=["read"],
            ),
        },
        categories={
            "bad": "google/gemini-2.5-flash",
            "good": "openai/gpt-4o",
        },
        run_id=run_id,
        read_root=read_root,
        overseer_interface=overseer,
        autonomy_level="max",
    )


def _two_task_workflow(
    policy: EscalationPolicy,
) -> WorkflowConfig:
    """Workflow: task_a (bad agent, will escalate) -> task_b (good agent).

    task_b depends on task_a, so they are in different groups.
    """
    task_a = TaskSpec(
        name="task_a",
        agent="bad-agent",
        task_prompt="Do something",
        timeout=10,
        context_refinement=False,
        retry=0,
    )
    task_b = TaskSpec(
        name="task_b",
        agent="good-agent",
        task_prompt="Do something else",
        timeout=10,
        context_refinement=False,
        retry=0,
    )
    return WorkflowConfig(
        name="test-workflow",
        description="Test escalation policy",
        tasks=[task_a, task_b],
        dependencies={"task_b": ["task_a"]},
        escalation_policy=policy,
    )


# -- Fixtures --------------------------------------------------------


@pytest.fixture
def run_id() -> UUID:
    return uuid6.uuid7()


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


# -- Tests -----------------------------------------------------------


@pytest.mark.asyncio
async def test_halt_policy_pauses_workflow(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """HALT policy pauses the scheduler after a group escalates."""
    scheduler = _make_mixed_scheduler(
        trace_writer, run_id, tmp_path,
    )
    config = _two_task_workflow(EscalationPolicy.HALT)
    await scheduler.execute_workflow(config)

    assert scheduler.is_paused

    # task_b should still be in CREATED state (never executed)
    task_b_ids = [
        tid
        for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        if spec.name == "task_b"
    ]
    assert len(task_b_ids) == 1
    task_b_state = scheduler._task_states[task_b_ids[0]]  # noqa: SLF001
    assert task_b_state == TaskState.CREATED

    # task_a should be escalated
    task_a_ids = [
        tid
        for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        if spec.name == "task_a"
    ]
    assert len(task_a_ids) == 1
    task_a_state = scheduler._task_states[task_a_ids[0]]  # noqa: SLF001
    assert task_a_state == TaskState.ESCALATED


@pytest.mark.asyncio
async def test_abort_all_policy_cancels_remaining(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """ABORT_ALL policy cancels active tasks and stops the workflow."""
    scheduler = _make_mixed_scheduler(
        trace_writer, run_id, tmp_path,
    )
    config = _two_task_workflow(EscalationPolicy.ABORT_ALL)
    await scheduler.execute_workflow(config)

    # task_a escalated (this happens before the policy kicks in)
    task_a_ids = [
        tid
        for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        if spec.name == "task_a"
    ]
    assert len(task_a_ids) == 1
    task_a_state = scheduler._task_states[task_a_ids[0]]  # noqa: SLF001
    assert task_a_state == TaskState.ESCALATED

    # task_b was never started, so abort() doesn't cancel it
    # (abort cancels only ACTIVE/PRECHECKING/POSTCHECKING).
    # But execution broke out of the group loop, so task_b
    # remains in CREATED state.
    task_b_ids = [
        tid
        for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        if spec.name == "task_b"
    ]
    assert len(task_b_ids) == 1
    task_b_state = scheduler._task_states[task_b_ids[0]]  # noqa: SLF001
    assert task_b_state == TaskState.CREATED


@pytest.mark.asyncio
async def test_continue_independent_runs_all_groups(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """CONTINUE_INDEPENDENT (default) keeps executing subsequent groups."""
    scheduler = _make_mixed_scheduler(
        trace_writer, run_id, tmp_path,
    )
    config = _two_task_workflow(
        EscalationPolicy.CONTINUE_INDEPENDENT,
    )
    await scheduler.execute_workflow(config)

    assert not scheduler.is_paused

    # task_a escalated
    task_a_ids = [
        tid
        for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        if spec.name == "task_a"
    ]
    task_a_state = scheduler._task_states[task_a_ids[0]]  # noqa: SLF001
    assert task_a_state == TaskState.ESCALATED

    # task_b was still executed (completed normally via good transport)
    task_b_ids = [
        tid
        for tid, spec in scheduler._task_specs.items()  # noqa: SLF001
        if spec.name == "task_b"
    ]
    assert len(task_b_ids) == 1
    task_b_state = scheduler._task_states[task_b_ids[0]]  # noqa: SLF001
    assert task_b_state == TaskState.COMPLETED


@pytest.mark.asyncio
async def test_parent_escalation_delivers_message(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """When a child escalates with an active parent session,
    the message goes to the parent session, not the Overseer."""
    mock_overseer = MockOverseerInterface()
    bad_transport = MockTransport()
    scheduler = Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": bad_transport},
        agents={"test-agent": make_agent()},
        categories={"default": "anthropic/claude-sonnet-4-6"},
        run_id=run_id,
        read_root=tmp_path,
        overseer_interface=mock_overseer,
        autonomy_level="max",
    )

    # Set up a fake parent task that is ACTIVE with a session
    parent_task_id = uuid6.uuid7()
    parent_session = MockParentSession()
    scheduler._task_states[parent_task_id] = TaskState.ACTIVE  # noqa: SLF001
    scheduler._task_specs[parent_task_id] = TaskSpec(  # noqa: SLF001
        name="parent_task",
        agent="test-agent",
        task_prompt="orchestrate",
        timeout=30,
        context_refinement=False,
    )
    scheduler._task_sessions[parent_task_id] = parent_session  # type: ignore[assignment]  # noqa: SLF001
    scheduler._task_parents[parent_task_id] = None  # noqa: SLF001
    scheduler._task_children[parent_task_id] = []  # noqa: SLF001

    # Execute a child task that will escalate
    child_task = TaskSpec(
        name="child_task",
        agent="test-agent",
        task_prompt="Do something",
        retry=0,
    )
    await scheduler.execute_task(
        child_task, parent_task_id,
    )

    # The parent session should have received the escalation message
    assert len(parent_session.messages) == 1
    assert "[ESCALATION]" in parent_session.messages[0]
    assert "child_task" in parent_session.messages[0]

    # The Overseer should NOT have received a TaskEscalated event
    escalation_events = [
        e for e in mock_overseer.events_sent
        if type(e).__name__ == "TaskEscalated"
    ]
    assert len(escalation_events) == 0


@pytest.mark.asyncio
async def test_service_start_stop_lifecycle(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Services are started before tasks and stopped after."""
    started: list[str] = []
    stopped: list[str] = []

    async def mock_start(
        config: ServiceConfig, work_dir: Path,
    ) -> ServiceInstance:
        started.append(config.name)
        return ServiceInstance(config=config)

    async def mock_stop(instance: ServiceInstance) -> None:
        stopped.append(instance.config.name)

    async def mock_check(instance: ServiceInstance) -> bool:
        return True

    monkeypatch.setattr(
        "orxtra.scheduler._executor.start_service",
        mock_start,
    )
    monkeypatch.setattr(
        "orxtra.scheduler._executor.stop_service",
        mock_stop,
    )
    monkeypatch.setattr(
        "orxtra.scheduler._executor.check_health",
        mock_check,
    )

    scheduler = _make_mixed_scheduler(
        trace_writer, run_id, tmp_path,
    )

    svc = ServiceConfig(
        name="test-db",
        start_command="echo start",
        stop_command="echo stop",
    )
    # Single task that completes to keep the workflow simple
    config = WorkflowConfig(
        name="svc-test",
        description="Test service lifecycle",
        tasks=[
            TaskSpec(
                name="t1",
                agent="good-agent",
                task_prompt="do it",
                timeout=10,
                context_refinement=False,
            ),
        ],
        dependencies={},
        services=[svc],
    )
    await scheduler.execute_workflow(config)

    assert started == ["test-db"]
    assert stopped == ["test-db"]


@pytest.mark.asyncio
async def test_service_stopped_on_failure(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Services are stopped even when a task escalates."""
    started: list[str] = []
    stopped: list[str] = []

    async def mock_start(
        config: ServiceConfig, work_dir: Path,
    ) -> ServiceInstance:
        started.append(config.name)
        return ServiceInstance(config=config)

    async def mock_stop(instance: ServiceInstance) -> None:
        stopped.append(instance.config.name)

    async def mock_check(instance: ServiceInstance) -> bool:
        return True

    monkeypatch.setattr(
        "orxtra.scheduler._executor.start_service",
        mock_start,
    )
    monkeypatch.setattr(
        "orxtra.scheduler._executor.stop_service",
        mock_stop,
    )
    monkeypatch.setattr(
        "orxtra.scheduler._executor.check_health",
        mock_check,
    )

    scheduler = _make_mixed_scheduler(
        trace_writer, run_id, tmp_path,
    )

    svc = ServiceConfig(
        name="test-db",
        start_command="echo start",
        stop_command="echo stop",
    )
    # Task uses bad-agent, will escalate
    config = WorkflowConfig(
        name="svc-fail-test",
        description="Test service cleanup on failure",
        tasks=[
            TaskSpec(
                name="failing",
                agent="bad-agent",
                task_prompt="do it",
                timeout=10,
                context_refinement=False,
            ),
        ],
        dependencies={},
        services=[svc],
        escalation_policy=EscalationPolicy.HALT,
    )
    await scheduler.execute_workflow(config)

    assert started == ["test-db"]
    # Services should be stopped in the finally block
    assert stopped == ["test-db"]


@pytest.mark.asyncio
async def test_create_workflow_wires_params(
    trace_writer: MockTraceWriter,
    run_id: UUID,
    tmp_path: Path,
) -> None:
    """handle_create_workflow passes postchecks, budget,
    description, and goals to the trace layer and task spec."""
    transport = MockTransport(auto_execute_tools=True)
    scheduler = Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},
        agents={"test-agent": make_agent()},
        categories={"default": "anthropic/claude-sonnet-4-6"},
        run_id=run_id,
        read_root=tmp_path,
        autonomy_level="max",
    )

    # Set up a fake active task so check_active_task succeeds
    parent_task_id = uuid6.uuid7()
    session_id = "test-session"
    scheduler._task_states[parent_task_id] = TaskState.ACTIVE  # noqa: SLF001
    scheduler._task_specs[parent_task_id] = TaskSpec(  # noqa: SLF001
        name="parent",
        agent="test-agent",
        task_prompt="orchestrate",
        timeout=30,
        context_refinement=False,
    )
    scheduler._active_tasks[session_id] = parent_task_id  # noqa: SLF001
    scheduler._task_parents[parent_task_id] = None  # noqa: SLF001
    scheduler._task_children[parent_task_id] = []  # noqa: SLF001

    from decimal import Decimal  # noqa: PLC0415

    params = CreateWorkflowParams(
        name="sub-workflow",
        description="Build the feature",
        goals=["pass tests", "lint clean"],
        postchecks=[],
        budget=Decimal("5.00"),
    )

    result = await scheduler.handle_create_workflow(
        session_id, params,
    )

    # Result should be a valid UUID string
    workflow_id = UUID(result)

    # Verify the task spec was stored with the right fields
    spec = scheduler._task_specs[workflow_id]  # noqa: SLF001
    assert spec.name == "sub-workflow"
    assert spec.budget == Decimal("5.00")
    assert spec.postchecks == []
    assert spec.subtasks == []

    # Verify trace_writer.create_task was called with config
    # containing description and goals
    create_calls = trace_writer.get_calls("create_task")
    workflow_calls = [
        c for c in create_calls
        if c["name"] == "sub-workflow"
    ]
    assert len(workflow_calls) == 1
    config_data = workflow_calls[0]["config"]
    assert config_data["description"] == "Build the feature"
    assert config_data["goals"] == ["pass tests", "lint clean"]

    # Verify parent-child relationship
    assert scheduler._task_parents[workflow_id] == parent_task_id  # noqa: SLF001
    assert workflow_id in scheduler._task_children[parent_task_id]  # noqa: SLF001
