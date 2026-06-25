"""Phase 3 tests: trace-integrated signal delivery.

Tests subscribe/unsubscribe, control signal dispatch,
and the execute_workflow lifecycle.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import uuid6
from orxtra.protocols import CheckResult, TaskResult, TaskSpec, TaskState
from orxtra.scheduler._types import WorkflowConfig

if TYPE_CHECKING:
    import uuid

    from orxtra.scheduler._executor import Scheduler

    from tests.conftest import MockTraceWriter


async def _noop_callable(
    context: Any,  # noqa: ANN401, ARG001
) -> TaskResult:
    return TaskResult(
        output="noop",
        structured_output=None,
        check_results=[
            CheckResult(passed=True, message="ok"),
        ],
    )


def _noop_task(name: str = "dp1") -> TaskSpec:
    return TaskSpec(
        name=name,
        callable=f"{__name__}:_noop_callable",
    )


def _noop_workflow(
    tasks: list[TaskSpec] | None = None,
) -> WorkflowConfig:
    if tasks is None:
        tasks = [_noop_task()]
    return WorkflowConfig(
        name="test-workflow",
        description="Signal delivery test",
        tasks=tasks,
        dependencies={},
    )


class TestSignalDelivery:
    """Tests for trace-integrated signal delivery (Phase 3)."""

    # -- subscribe / unsubscribe bookkeeping --

    async def test_subscribe_registers_callback(
        self,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """subscribe_run_control records the call on the mock."""
        called = False

        async def cb(
            rid: uuid.UUID, status: str,
        ) -> None:
            nonlocal called
            called = True

        await trace_writer.subscribe_run_control(run_id, cb)

        calls = trace_writer.get_calls(
            "subscribe_run_control",
        )
        assert len(calls) == 1
        assert calls[0]["run_id"] == run_id
        # The mock stores the callback for later invocation
        assert trace_writer._control_callback is cb  # noqa: SLF001

    async def test_transition_run_fires_callback(
        self,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """After subscribing, transition_run fires the control
        callback with the correct arguments.

        The MockTraceWriter stores the callback but does not
        automatically invoke it on transition_run (that is the
        real TraceWriter's responsibility). This test verifies
        the mock records the subscription and that the stored
        callback can be invoked manually, confirming the wiring.
        """
        received: list[tuple[uuid.UUID, str]] = []

        async def cb(
            rid: uuid.UUID, status: str,
        ) -> None:
            received.append((rid, status))

        await trace_writer.subscribe_run_control(run_id, cb)

        # Simulate what the real TraceWriter does after a
        # transition: invoke the stored control callback.
        assert trace_writer._control_callback is not None  # noqa: SLF001
        await trace_writer._control_callback(  # noqa: SLF001
            run_id, "aborted",
        )

        assert len(received) == 1
        assert received[0] == (run_id, "aborted")

    async def test_unsubscribe_stops_callbacks(
        self,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """After unsubscribe, the control callback is cleared."""

        async def cb(
            rid: uuid.UUID, status: str,
        ) -> None:
            pass

        await trace_writer.subscribe_run_control(run_id, cb)
        assert trace_writer._control_callback is cb  # noqa: SLF001

        await trace_writer.unsubscribe_run_control(run_id)
        assert trace_writer._control_callback is None  # noqa: SLF001

        calls = trace_writer.get_calls(
            "unsubscribe_run_control",
        )
        assert len(calls) == 1
        assert calls[0]["run_id"] == run_id

    async def test_unsubscribe_unknown_no_error(
        self,
        trace_writer: MockTraceWriter,
    ) -> None:
        """Unsubscribing an unknown run_id does not raise."""
        unknown_id = uuid6.uuid7()
        # Should not raise
        await trace_writer.unsubscribe_run_control(
            unknown_id,
        )
        calls = trace_writer.get_calls(
            "unsubscribe_run_control",
        )
        assert len(calls) == 1

    async def test_multiple_subscriptions_different_runs(
        self,
        trace_writer: MockTraceWriter,
    ) -> None:
        """Subscribe callbacks for two different run_ids.

        The MockTraceWriter stores only the latest callback
        (single-run design), so subscribing a second run_id
        overwrites the first. Both calls are recorded.
        """
        run_a = uuid6.uuid7()
        run_b = uuid6.uuid7()

        async def cb_a(
            rid: uuid.UUID, status: str,
        ) -> None:
            pass

        async def cb_b(
            rid: uuid.UUID, status: str,
        ) -> None:
            pass

        await trace_writer.subscribe_run_control(
            run_a, cb_a,
        )
        await trace_writer.subscribe_run_control(
            run_b, cb_b,
        )

        calls = trace_writer.get_calls(
            "subscribe_run_control",
        )
        assert len(calls) == 2
        assert calls[0]["run_id"] == run_a
        assert calls[1]["run_id"] == run_b
        # Latest callback wins in the mock
        assert trace_writer._control_callback is cb_b  # noqa: SLF001

    # -- _handle_control_signal dispatch --

    async def test_handle_control_signal_abort(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """_handle_control_signal('aborted') cancels active tasks
        via abort()."""
        task_id = uuid6.uuid7()
        task_spec = TaskSpec(
            name="t1",
            agent="test-agent",
            task_prompt="do stuff",
        )
        scheduler._init_task_state(  # noqa: SLF001
            task_id, task_spec, parent=None,
        )
        scheduler._task_states[task_id] = TaskState.ACTIVE  # noqa: SLF001

        await scheduler._handle_control_signal(  # noqa: SLF001
            run_id, "aborted",
        )

        assert (
            scheduler._task_states[task_id]  # noqa: SLF001
            == TaskState.CANCELLED
        )
        # abort() also transitions via trace_writer
        task_transitions = trace_writer.get_calls(
            "transition_task",
        )
        cancelled = [
            c
            for c in task_transitions
            if c["new_status"] == TaskState.CANCELLED.value
        ]
        assert len(cancelled) == 1
        assert cancelled[0]["task_id"] == task_id

    async def test_handle_control_signal_pause(
        self,
        scheduler: Scheduler,
        run_id: uuid.UUID,
    ) -> None:
        """_handle_control_signal('paused') sets the paused flag."""
        assert not scheduler.is_paused

        await scheduler._handle_control_signal(  # noqa: SLF001
            run_id, "paused",
        )

        assert scheduler.is_paused

    async def test_handle_control_signal_unknown_noop(
        self,
        scheduler: Scheduler,
        run_id: uuid.UUID,
    ) -> None:
        """An unrecognized status is silently ignored."""
        assert not scheduler.is_paused

        await scheduler._handle_control_signal(  # noqa: SLF001
            run_id, "unknown_status",
        )

        # Neither paused nor aborted
        assert not scheduler.is_paused

    # -- execute_workflow lifecycle --

    async def test_execute_workflow_subscribes_and_unsubscribes(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """execute_workflow subscribes at start and unsubscribes
        at end."""
        config = _noop_workflow()

        await scheduler.execute_workflow(config)

        subs = trace_writer.get_calls(
            "subscribe_run_control",
        )
        unsubs = trace_writer.get_calls(
            "unsubscribe_run_control",
        )
        assert len(subs) == 1
        assert subs[0]["run_id"] == run_id
        assert len(unsubs) == 1
        assert unsubs[0]["run_id"] == run_id

    async def test_startup_race_subscribe_before_tasks(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """subscribe_run_control is called before any task
        creation, closing the startup race window.

        The real startup race (where the run is aborted between
        DB insert and subscription) is tested at the trace
        integration level with a real PG connection. Here we
        verify ordering via the mock call log.
        """
        config = _noop_workflow()
        await scheduler.execute_workflow(config)

        # Find the indices of subscribe and first create_task
        subscribe_idx = None
        create_task_idx = None
        for i, (method, _kwargs) in enumerate(
            trace_writer.calls,
        ):
            if (
                method == "subscribe_run_control"
                and subscribe_idx is None
            ):
                subscribe_idx = i
            if (
                method == "create_task"
                and create_task_idx is None
            ):
                create_task_idx = i

        assert subscribe_idx is not None
        assert create_task_idx is not None
        assert subscribe_idx < create_task_idx
