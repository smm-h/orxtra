"""End-to-end integration tests for wait_for tasks through dispatch's EventDelivery.

Tests verify the full flow: Scheduler receives a workflow containing
wait_for tasks, delegates to the EventDelivery implementation, and
correctly transitions task state on fire or timeout.
"""

from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxtra.dispatch import (
    DualPhaseEventDelivery,
    InMemoryDispatchBackend,
    TransientEventDelivery,
)
from orxtra.protocols import (
    CheckResult,
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
)
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._types import WorkflowConfig

if TYPE_CHECKING:
    import uuid

from .conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)


def _make_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: uuid.UUID,
    read_root: Path,
    *,
    event_delivery: TransientEventDelivery
    | DualPhaseEventDelivery
    | None = None,
) -> Scheduler:
    """Create a headless scheduler with explicit event_delivery."""
    return Scheduler(
        trace_writer=trace_writer,
        transport_registry={"anthropic": transport},
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=read_root,
        autonomy_level="max",
        event_delivery=event_delivery,
    )


# -- Helpers for callable tasks used in dependency tests --

_CALLABLE_MODULE = "test_wait_for_helpers"


def _register_callable(
    name: str,
    func: object,
) -> str:
    """Register a callable function in a synthetic module
    and return the dotted path ``module:function``."""
    if _CALLABLE_MODULE not in sys.modules:
        sys.modules[_CALLABLE_MODULE] = types.ModuleType(
            _CALLABLE_MODULE,
        )
    setattr(sys.modules[_CALLABLE_MODULE], name, func)
    return f"{_CALLABLE_MODULE}:{name}"


@pytest.fixture(autouse=True)
def _cleanup_callable_module() -> None:
    """Remove synthetic module after each test."""
    yield  # type: ignore[misc]
    sys.modules.pop(_CALLABLE_MODULE, None)


class TestWaitForCompletesOnFire:
    """wait_for task completes when the event is fired."""

    async def test_wait_for_completes_on_fire(
        self, tmp_path: Path,
    ) -> None:
        delivery = TransientEventDelivery()
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_scheduler(
            trace, transport, run_id, tmp_path,
            event_delivery=delivery,
        )

        task = TaskSpec(
            name="wait-task",
            wait_for="deploy_done",
            timeout=5,
        )
        config = WorkflowConfig(
            name="wait-wf",
            description="Wait-for fire test",
            tasks=[task],
            dependencies={},
        )

        payload = {"status": "ok", "version": "1.2.3"}

        async def run_workflow() -> None:
            await sched.execute_workflow(config)

        wf_task = asyncio.create_task(run_workflow())

        # Yield control so the scheduler registers the waiter.
        await asyncio.sleep(0.05)
        await delivery.fire("deploy_done", payload)

        await asyncio.wait_for(wf_task, timeout=5.0)

        # The single task should be COMPLETED.
        states = list(sched._task_states.values())  # noqa: SLF001
        assert len(states) == 1
        assert states[0] == TaskState.COMPLETED

        # Verify the trace recorded the COMPLETED transition.
        completed_transitions = [
            c
            for c in trace.get_calls("transition_task")
            if c.get("new_status") == "completed"
        ]
        assert len(completed_transitions) >= 1


class TestWaitForTimesOut:
    """wait_for task transitions to CANCELLED on timeout."""

    async def test_wait_for_times_out(
        self, tmp_path: Path,
    ) -> None:
        delivery = TransientEventDelivery()
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_scheduler(
            trace, transport, run_id, tmp_path,
            event_delivery=delivery,
        )

        # Very short timeout; do not fire the event.
        task = TaskSpec(
            name="wait-timeout",
            wait_for="never_fired",
            timeout=1,
        )
        config = WorkflowConfig(
            name="timeout-wf",
            description="Wait-for timeout test",
            tasks=[task],
            dependencies={},
        )

        await sched.execute_workflow(config)

        states = list(sched._task_states.values())  # noqa: SLF001
        assert len(states) == 1
        assert states[0] == TaskState.CANCELLED

        # Verify the trace recorded the CANCELLED transition.
        cancelled_transitions = [
            c
            for c in trace.get_calls("transition_task")
            if c.get("new_status") == "cancelled"
        ]
        assert len(cancelled_transitions) >= 1


class TestWaitForWithDependencies:
    """wait_for task with dependency chain: A -> B(wait_for) -> C."""

    async def test_wait_for_with_dependencies(
        self, tmp_path: Path,
    ) -> None:
        delivery = TransientEventDelivery()
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_scheduler(
            trace, transport, run_id, tmp_path,
            event_delivery=delivery,
        )

        # Track execution order.
        execution_order: list[str] = []

        async def task_a_fn(ctx: TaskContext) -> TaskResult:
            execution_order.append("A")
            return TaskResult(
                output="A done",
                structured_output={"step": "A"},
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        async def task_c_fn(ctx: TaskContext) -> TaskResult:
            execution_order.append("C")
            return TaskResult(
                output="C done",
                structured_output={"step": "C"},
                check_results=[
                    CheckResult(passed=True, message="ok"),
                ],
            )

        task_a_path = _register_callable("task_a", task_a_fn)
        task_c_path = _register_callable("task_c", task_c_fn)

        task_a = TaskSpec(
            name="task-a",
            callable=task_a_path,
        )
        task_b = TaskSpec(
            name="task-b",
            wait_for="middle_event",
            timeout=5,
            depends_on=["task-a"],
        )
        task_c = TaskSpec(
            name="task-c",
            callable=task_c_path,
            depends_on=["task-b"],
        )

        config = WorkflowConfig(
            name="dep-wf",
            description="Dependency chain with wait_for",
            tasks=[task_a, task_b, task_c],
            dependencies={
                "task-b": ["task-a"],
                "task-c": ["task-b"],
            },
        )

        async def run_workflow() -> None:
            await sched.execute_workflow(config)

        wf_task = asyncio.create_task(run_workflow())

        # Wait for task A to complete and task B to
        # start waiting.
        await asyncio.sleep(0.1)
        assert "A" in execution_order

        # Fire the event so task B completes.
        await delivery.fire(
            "middle_event", {"fired": True},
        )

        await asyncio.wait_for(wf_task, timeout=5.0)

        # All three tasks should be COMPLETED.
        assert len(sched._task_states) == 3  # noqa: SLF001
        for state in sched._task_states.values():  # noqa: SLF001
            assert state == TaskState.COMPLETED

        # C must have run after B.
        assert execution_order == ["A", "C"]


class TestWaitForWithDualPhase:
    """wait_for works through DualPhaseEventDelivery."""

    async def test_dual_phase_fire_completes_wait(
        self, tmp_path: Path,
    ) -> None:
        backend = InMemoryDispatchBackend()
        delivery = DualPhaseEventDelivery(backend=backend)
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_scheduler(
            trace, transport, run_id, tmp_path,
            event_delivery=delivery,
        )

        task = TaskSpec(
            name="dual-wait",
            wait_for="dual_event",
            timeout=5,
        )
        config = WorkflowConfig(
            name="dual-wf",
            description="Dual-phase wait test",
            tasks=[task],
            dependencies={},
        )

        payload = {"source": "dual", "ok": True}

        async def run_workflow() -> None:
            await sched.execute_workflow(config)

        wf_task = asyncio.create_task(run_workflow())

        # Yield so the scheduler registers the waiter.
        await asyncio.sleep(0.05)
        await delivery.fire("dual_event", payload)

        await asyncio.wait_for(wf_task, timeout=5.0)

        states = list(sched._task_states.values())  # noqa: SLF001
        assert len(states) == 1
        assert states[0] == TaskState.COMPLETED

    async def test_dual_phase_timeout(
        self, tmp_path: Path,
    ) -> None:
        backend = InMemoryDispatchBackend()
        delivery = DualPhaseEventDelivery(backend=backend)
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_scheduler(
            trace, transport, run_id, tmp_path,
            event_delivery=delivery,
        )

        task = TaskSpec(
            name="dual-timeout",
            wait_for="never_arrives",
            timeout=1,
        )
        config = WorkflowConfig(
            name="dual-timeout-wf",
            description="Dual-phase timeout test",
            tasks=[task],
            dependencies={},
        )

        await sched.execute_workflow(config)

        states = list(sched._task_states.values())  # noqa: SLF001
        assert len(states) == 1
        assert states[0] == TaskState.CANCELLED
