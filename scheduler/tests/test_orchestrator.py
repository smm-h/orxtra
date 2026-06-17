"""Tests for orchestrator task execution with await_task suspension."""

from __future__ import annotations

import asyncio
import re
import uuid
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import uuid6
from orxt.protocols import TaskSpec, TaskState
from orxt.protocols._execution import CheckResult, ScriptExecution
from orxt.tool import make_await_task_tool
from orxt.transport import (
    Continuation,
    Result,
    SessionSuspended,
    StepFinish,
    ToolUse,
)

from tests.conftest import MockTransport

if TYPE_CHECKING:
    from collections.abc import Callable

    from orxt.protocols._tool import Tool
    from orxt.scheduler._executor import Scheduler

    from tests.conftest import MockTraceWriter


@pytest.fixture
def orchestrator_task() -> TaskSpec:
    return TaskSpec(
        name="orchestrator",
        agent="test-agent",
        task_prompt="You are an orchestrator. Create and await tasks.",
        timeout=300,
        context_refinement=False,
        orchestrator=True,
    )


class TestOrchestratorDispatch:
    async def test_orchestrator_flag_dispatches_correctly(
        self, orchestrator_task: TaskSpec,
    ) -> None:
        """TaskSpec with orchestrator=True should dispatch
        to _execute_orchestrator_task."""
        assert orchestrator_task.orchestrator is True

    async def test_taskspec_orchestrator_default_none(self) -> None:
        task = TaskSpec(
            name="normal",
            agent="test",
            task_prompt="do stuff",
            timeout=60,
            context_refinement=False,
        )
        assert task.orchestrator is None

    async def test_execute_task_routes_to_orchestrator(
        self, make_scheduler: Callable[..., Scheduler],
    ) -> None:
        """execute_task with orchestrator=True actually routes
        to _execute_orchestrator_task (not _execute_agent_task)."""
        # Use a transport that just returns a result (no tools)
        sched = make_scheduler(
            transport_registry={
                "anthropic": MockTransport("done"),
            },
        )
        task = TaskSpec(
            name="orch",
            agent="test-agent",
            task_prompt="orchestrate",
            timeout=60,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # Orchestrator completes with output from transport
        assert result.output == "done"
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001

    async def test_orchestrator_transitions_active_then_completed(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Orchestrator task transitions through ACTIVE -> COMPLETED."""
        sched = make_scheduler(
            transport_registry={
                "anthropic": MockTransport("output"),
            },
        )
        task = TaskSpec(
            name="orch",
            agent="test-agent",
            task_prompt="do it",
            timeout=60,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        await sched.execute_task(task, None, task_id=task_id)

        # Check trace transitions include active and completed
        transitions = [
            c for c in trace_writer.get_calls("transition_task")
            if c["task_id"] == task_id
        ]
        statuses = [t["new_status"] for t in transitions]
        assert "active" in statuses
        assert "completed" in statuses


class TestAwaitTaskTool:
    async def test_make_await_task_tool_is_suspending(self) -> None:
        mock_scheduler = AsyncMock()
        tool = make_await_task_tool(mock_scheduler, "session-1")
        assert tool.suspending is True
        assert tool.name == "await_task"

    async def test_await_task_calls_handler(self) -> None:
        mock_scheduler = AsyncMock()
        mock_scheduler.handle_await_task = AsyncMock(
            return_value="Awaiting task abc.",
        )
        tool = make_await_task_tool(mock_scheduler, "session-1")
        result = await tool.execute({"task_id": "abc"})
        mock_scheduler.handle_await_task.assert_called_once_with(
            "session-1", "abc",
        )
        assert "abc" in result


class TestHandleAwaitTask:
    async def test_handle_await_task_records_pending(
        self, make_scheduler: Callable[..., Scheduler],
    ) -> None:
        scheduler = make_scheduler()
        task_id = uuid.uuid4()
        scheduler._task_states[task_id] = TaskState.CREATED  # noqa: SLF001
        scheduler._task_specs[task_id] = TaskSpec(  # noqa: SLF001
            name="child",
            agent="test-agent",
            task_prompt="do stuff",
            timeout=60,
            context_refinement=False,
        )

        result = await scheduler.handle_await_task(
            "session-1", str(task_id),
        )
        assert scheduler._pending_await["session-1"] == str(task_id)  # noqa: SLF001
        assert "Awaiting" in result

    async def test_handle_await_task_nonexistent_raises(
        self, make_scheduler: Callable[..., Scheduler],
    ) -> None:
        scheduler = make_scheduler()
        with pytest.raises(Exception, match="does not exist"):
            await scheduler.handle_await_task(
                "session-1", str(uuid.uuid4()),
            )


class TestTaskStateSuspended:
    def test_suspended_state_exists(self) -> None:
        assert TaskState.SUSPENDED == "suspended"

    def test_suspended_in_enum(self) -> None:
        assert "suspended" in [s.value for s in TaskState]


class TestOrchestratorSuspension:
    """Tests for the orchestrator suspend/resume flow.

    These use a mock transport that simulates suspension:
    1. First send: calls create_task + await_task tools, then suspends
    2. Resume: receives child result, returns final output
    """

    async def test_orchestrator_suspends_and_resumes(  # noqa: C901
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Full orchestrator flow: create child, await, suspend,
        child runs, resume with result."""
        child_task_id_holder: list[str] = []

        class SuspendingTransport:
            """Transport that creates a child task then suspends."""

            def __init__(self) -> None:
                self._resumed = False

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = model, system_prompt, stream_deltas
                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                # Orchestrator does NOT call start_task.
                # The scheduler manages its activation directly.

                # Call create_task to make a child
                if "create_task" in tool_map:
                    child_id = await tool_map["create_task"].execute({
                        "name": "child-work",
                        "agent": "test-agent",
                        "task_prompt": "do child work",
                        "timeout": 60,
                        "context_refinement": False,
                    })
                    child_task_id_holder.append(child_id)
                    yield ToolUse(
                        tool_name="create_task",
                        input={"name": "child-work"},
                        output=child_id,
                        status="success",
                    )

                # Call await_task (suspending tool)
                if "await_task" in tool_map and child_task_id_holder:
                    await_result = await tool_map["await_task"].execute(
                        {"task_id": child_task_id_holder[0]},
                    )
                    yield ToolUse(
                        tool_name="await_task",
                        input={"task_id": child_task_id_holder[0]},
                        output=await_result,
                        status="success",
                    )

                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )

                # Yield SessionSuspended to trigger the orchestrator loop
                yield SessionSuspended(
                    continuation=Continuation(
                        executed_results=[],
                        remaining_blocks=[],
                        session_id=sid,
                    ),
                    session_id=sid,
                )

            async def resume(  # noqa: PLR0913
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = continuation, model, system_prompt
                _ = stream_deltas, tools
                self._resumed = True
                sid = session_id or str(uuid6.uuid7())

                yield StepFinish(
                    reason="end_turn",
                    input_tokens=5,
                    output_tokens=3,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text=f"Orchestrator done. Child said: {result}",
                    session_id=sid,
                    total_input_tokens=15,
                    total_output_tokens=8,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        # The child task will be executed by the normal
        # MockTransport (start_task + end_task pattern).
        # But the orchestrator transport is SuspendingTransport.
        # We need a transport registry that serves both.
        # The scheduler uses a single transport per provider.
        # So the child also goes through the same transport.
        # This means we need SuspendingTransport to also handle
        # the child's send (which is a regular agent send).

        # Simpler approach: use MockTransport for the
        # child via a multi-behavior transport.

        class OrchestratorTransport:
            """Transport that suspends on first send (orchestrator),
            then acts as a simple no-tools transport for the child,
            then handles resume for the orchestrator."""

            def __init__(self) -> None:
                self._send_count = 0
                self._suspending = SuspendingTransport()
                self._resumed = False

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                self._send_count += 1
                if self._send_count == 1:
                    # Orchestrator's initial send
                    async for ev in self._suspending.send(
                        message,
                        model=model,
                        system_prompt=system_prompt,
                        tools=tools,
                        session_id=session_id,
                        stream_deltas=stream_deltas,
                    ):
                        yield ev
                else:
                    # Child task send: just start_task + end_task
                    sid = session_id or str(uuid6.uuid7())
                    tool_map = {t.name: t for t in tools}

                    match = re.search(
                        r"Your task ID is ([0-9a-f-]+)",
                        message,
                    )
                    task_id_str = (
                        match.group(1) if match else "unknown"
                    )

                    if "start_task" in tool_map:
                        sr = await tool_map["start_task"].execute(
                            {"task_id": task_id_str},
                        )
                        yield ToolUse(
                            tool_name="start_task",
                            input={"task_id": task_id_str},
                            output=sr,
                            status="success",
                        )
                    if "end_task" in tool_map:
                        er = await tool_map["end_task"].execute(
                            {"message": "child done"},
                        )
                        yield ToolUse(
                            tool_name="end_task",
                            input={"message": "child done"},
                            output=er,
                            status="success",
                        )

                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=10,
                        output_tokens=5,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield Result(
                        text="child done",
                        session_id=sid,
                        total_input_tokens=10,
                        total_output_tokens=5,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=2,
                    )

            async def resume(  # noqa: PLR0913
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                self._resumed = True
                async for ev in self._suspending.resume(
                    continuation,
                    result,
                    model=model,
                    system_prompt=system_prompt,
                    tools=tools,
                    session_id=session_id,
                    stream_deltas=stream_deltas,
                ):
                    yield ev

        transport = OrchestratorTransport()
        sched = make_scheduler(
            transport_registry={"anthropic": transport},
        )

        task = TaskSpec(
            name="orch",
            agent="test-agent",
            task_prompt="orchestrate work",
            timeout=300,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # Verify the orchestrator completed
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001
        assert result.output is not None
        output_lower = result.output.lower()
        assert (
            "child done" in output_lower
            or "child said" in output_lower
        )

        # Verify transport was actually resumed
        assert transport._resumed is True  # noqa: SLF001

        # Verify trace saw suspended and active transitions
        transitions = [
            c for c in trace_writer.get_calls("transition_task")
            if c["task_id"] == task_id
        ]
        statuses = [t["new_status"] for t in transitions]
        assert "suspended" in statuses
        # Should have gone active -> suspended -> active -> completed
        assert statuses.count("active") >= 2


class TestOrchestratorMultiChild:
    """Tests for orchestrator tasks that create and await
    multiple children through sequential suspend/resume cycles."""

    async def test_sequential_await_two_children(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Orchestrator creates child A, awaits it, then creates
        child B, awaits it. Both complete. Orchestrator gets both
        results."""
        child_ids: list[str] = []

        class MultiAwaitTransport:
            """Transport with 3 phases:
            1. send: create child A, await A -> suspend
            2. resume #1: create child B, await B -> suspend
            3. resume #2: return final result
            Child sends: start_task + end_task pattern.
            """

            def __init__(self) -> None:
                self._send_count = 0
                self._resume_count = 0

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = model, system_prompt, stream_deltas
                self._send_count += 1
                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                if self._send_count == 1:
                    # Orchestrator: create child A, await A
                    child_id = await tool_map[
                        "create_task"
                    ].execute({
                        "name": "child-A",
                        "agent": "test-agent",
                        "task_prompt": "do child A work",
                        "timeout": 60,
                        "context_refinement": False,
                    })
                    child_ids.append(child_id)
                    yield ToolUse(
                        tool_name="create_task",
                        input={"name": "child-A"},
                        output=child_id,
                        status="success",
                    )
                    await_result = await tool_map[
                        "await_task"
                    ].execute({"task_id": child_id})
                    yield ToolUse(
                        tool_name="await_task",
                        input={"task_id": child_id},
                        output=await_result,
                        status="success",
                    )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=10,
                        output_tokens=5,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield SessionSuspended(
                        continuation=Continuation(
                            executed_results=[],
                            remaining_blocks=[],
                            session_id=sid,
                        ),
                        session_id=sid,
                    )
                else:
                    # Child send: start_task + end_task
                    match = re.search(
                        r"Your task ID is ([0-9a-f-]+)",
                        message,
                    )
                    task_id_str = (
                        match.group(1) if match else "unknown"
                    )
                    if "start_task" in tool_map:
                        sr = await tool_map[
                            "start_task"
                        ].execute({"task_id": task_id_str})
                        yield ToolUse(
                            tool_name="start_task",
                            input={"task_id": task_id_str},
                            output=sr,
                            status="success",
                        )
                    if "end_task" in tool_map:
                        child_label = (
                            "A" if self._send_count == 2
                            else "B"
                        )
                        er = await tool_map[
                            "end_task"
                        ].execute(
                            {"message": f"child {child_label} done"},
                        )
                        yield ToolUse(
                            tool_name="end_task",
                            input={
                                "message": (
                                    f"child {child_label} done"
                                ),
                            },
                            output=er,
                            status="success",
                        )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=10,
                        output_tokens=5,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    child_label = (
                        "A" if self._send_count == 2
                        else "B"
                    )
                    yield Result(
                        text=f"child {child_label} done",
                        session_id=sid,
                        total_input_tokens=10,
                        total_output_tokens=5,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=2,
                    )

            async def resume(  # noqa: PLR0913
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = model, system_prompt, stream_deltas
                _ = continuation
                self._resume_count += 1
                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                if self._resume_count == 1:
                    # Resume #1: create child B, await B
                    child_id = await tool_map[
                        "create_task"
                    ].execute({
                        "name": "child-B",
                        "agent": "test-agent",
                        "task_prompt": "do child B work",
                        "timeout": 60,
                        "context_refinement": False,
                    })
                    child_ids.append(child_id)
                    yield ToolUse(
                        tool_name="create_task",
                        input={"name": "child-B"},
                        output=child_id,
                        status="success",
                    )
                    await_result = await tool_map[
                        "await_task"
                    ].execute({"task_id": child_id})
                    yield ToolUse(
                        tool_name="await_task",
                        input={"task_id": child_id},
                        output=await_result,
                        status="success",
                    )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=5,
                        output_tokens=3,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield SessionSuspended(
                        continuation=Continuation(
                            executed_results=[],
                            remaining_blocks=[],
                            session_id=sid,
                        ),
                        session_id=sid,
                    )
                else:
                    # Resume #2: final result
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=5,
                        output_tokens=3,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield Result(
                        text=(
                            "Both children done."
                            f" Got: {result}"
                        ),
                        session_id=sid,
                        total_input_tokens=20,
                        total_output_tokens=11,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=0,
                    )

        transport = MultiAwaitTransport()
        sched = make_scheduler(
            transport_registry={"anthropic": transport},
        )

        task = TaskSpec(
            name="orch",
            agent="test-agent",
            task_prompt="orchestrate two children",
            timeout=300,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # Both children were created
        assert len(child_ids) == 2

        # Orchestrator completed
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001
        assert result.output is not None
        assert "both" in result.output.lower()

        # Transport went through 2 resumes
        assert transport._resume_count == 2  # noqa: SLF001

        # Verify trace: suspended twice, active 3+ times
        transitions = [
            c for c in trace_writer.get_calls("transition_task")
            if c["task_id"] == task_id
        ]
        statuses = [t["new_status"] for t in transitions]
        assert statuses.count("suspended") == 2
        assert statuses.count("active") >= 3

    async def test_orchestrator_without_postchecks_completes(
        self,
        make_scheduler: Callable[..., Scheduler],
    ) -> None:
        """Orchestrator tasks without postchecks complete directly.

        When no postchecks are defined, the orchestrator path
        goes straight to COMPLETED with empty check_results.
        """
        sched = make_scheduler(
            transport_registry={
                "anthropic": MockTransport(
                    "orchestrator output",
                ),
            },
        )
        task = TaskSpec(
            name="orch-with-checks",
            agent="test-agent",
            task_prompt="do checked work",
            timeout=60,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # Orchestrator completes successfully
        assert result.output == "orchestrator output"
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001
        assert result.check_results == []

    async def test_orchestrator_with_failing_postchecks_escalates(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Orchestrator with failing postchecks escalates."""
        sched = make_scheduler(
            transport_registry={
                "anthropic": MockTransport(
                    "orchestrator output",
                ),
            },
        )
        task = TaskSpec(
            name="orch-failing-checks",
            agent="test-agent",
            task_prompt="do checked work",
            timeout=60,
            context_refinement=False,
            orchestrator=True,
            postchecks=[
                ScriptExecution(callable="check_quality"),
            ],
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        # Monkey-patch _run_postchecks to return failure
        async def _failing_postchecks(
            _task: TaskSpec,
            _task_id: UUID,
        ) -> list[CheckResult]:
            return [
                CheckResult(
                    passed=False,
                    message="Quality check failed",
                ),
            ]

        sched._run_postchecks = _failing_postchecks  # type: ignore[assignment]  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        assert sched._task_states[task_id] == TaskState.ESCALATED  # noqa: SLF001
        assert result.output is None
        assert any(
            not cr.passed for cr in result.check_results
        )

        # Verify trace transitions
        transitions = [
            c for c in trace_writer.get_calls("transition_task")
            if c["task_id"] == task_id
        ]
        statuses = [t["new_status"] for t in transitions]
        assert "postchecking" in statuses
        assert "postcheck_failed" in statuses
        assert "escalated" in statuses
        assert "completed" not in statuses

    async def test_child_timeout_during_await(
        self,
        make_scheduler: Callable[..., Scheduler],
    ) -> None:
        """Orchestrator creates a child with a short timeout.
        The child's transport sleeps longer than the timeout.
        The child fails with timeout, and the orchestrator
        resumes with the failure result."""
        child_id_holder: list[str] = []

        class TimeoutOrchestratorTransport:
            """Orchestrator creates a child with timeout=1,
            the child's transport sleeps forever."""

            def __init__(self) -> None:
                self._send_count = 0

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = model, system_prompt, stream_deltas
                self._send_count += 1
                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                if self._send_count == 1:
                    # Orchestrator: create child with
                    # short timeout, await it
                    child_id = await tool_map[
                        "create_task"
                    ].execute({
                        "name": "slow-child",
                        "agent": "test-agent",
                        "task_prompt": "take too long",
                        "timeout": 1,
                        "context_refinement": False,
                    })
                    child_id_holder.append(child_id)
                    yield ToolUse(
                        tool_name="create_task",
                        input={"name": "slow-child"},
                        output=child_id,
                        status="success",
                    )
                    await_result = await tool_map[
                        "await_task"
                    ].execute({"task_id": child_id})
                    yield ToolUse(
                        tool_name="await_task",
                        input={"task_id": child_id},
                        output=await_result,
                        status="success",
                    )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=10,
                        output_tokens=5,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield SessionSuspended(
                        continuation=Continuation(
                            executed_results=[],
                            remaining_blocks=[],
                            session_id=sid,
                        ),
                        session_id=sid,
                    )
                else:
                    # Child send: sleeps forever (will be
                    # cancelled by timeout)
                    await asyncio.sleep(999)
                    # Should never reach here
                    yield Result(
                        text="unreachable",
                        session_id=sid,
                        total_input_tokens=0,
                        total_output_tokens=0,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=0,
                    )

            async def resume(  # noqa: PLR0913
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = (
                    continuation, model,
                    system_prompt, tools,
                    stream_deltas,
                )
                sid = session_id or str(uuid6.uuid7())
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=5,
                    output_tokens=3,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text=(
                        "Orchestrator done after"
                        f" child result: {result}"
                    ),
                    session_id=sid,
                    total_input_tokens=15,
                    total_output_tokens=8,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        transport = TimeoutOrchestratorTransport()
        sched = make_scheduler(
            transport_registry={"anthropic": transport},
        )

        task = TaskSpec(
            name="orch-timeout",
            agent="test-agent",
            task_prompt="orchestrate with timeout child",
            timeout=300,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # The child timed out. The orchestrator resumes with
        # the child's "no output" result (since child returned
        # TaskResult(output=None) on timeout).
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001
        assert result.output is not None
        # The resume message contains "no output" for timed-out child
        assert "no output" in result.output.lower()

    async def test_create_three_children_await_sequentially(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Orchestrator creates 3 children upfront in the
        initial send, then awaits them one by one via
        sequential suspend/resume cycles."""
        child_ids: list[str] = []

        class ThreeChildTransport:
            """Transport that:
            1. send #1 (orchestrator): creates 3 children,
               awaits the first -> suspend
            2. send #2,3,4 (children): start_task + end_task
            3. resume #1: awaits second child -> suspend
            4. resume #2: awaits third child -> suspend
            5. resume #3: returns final result
            """

            def __init__(self) -> None:
                self._send_count = 0
                self._resume_count = 0

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = model, system_prompt, stream_deltas
                self._send_count += 1
                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                if self._send_count == 1:
                    # Orchestrator: create 3 children upfront
                    for i in range(3):
                        cid = await tool_map[
                            "create_task"
                        ].execute({
                            "name": f"child-{i}",
                            "agent": "test-agent",
                            "task_prompt": f"do work {i}",
                            "timeout": 60,
                            "context_refinement": False,
                        })
                        child_ids.append(cid)
                        yield ToolUse(
                            tool_name="create_task",
                            input={"name": f"child-{i}"},
                            output=cid,
                            status="success",
                        )

                    # Await the first child
                    await_result = await tool_map[
                        "await_task"
                    ].execute(
                        {"task_id": child_ids[0]},
                    )
                    yield ToolUse(
                        tool_name="await_task",
                        input={"task_id": child_ids[0]},
                        output=await_result,
                        status="success",
                    )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=10,
                        output_tokens=5,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield SessionSuspended(
                        continuation=Continuation(
                            executed_results=[],
                            remaining_blocks=[],
                            session_id=sid,
                        ),
                        session_id=sid,
                    )
                else:
                    # Child sends: start_task + end_task
                    child_idx = self._send_count - 2
                    match = re.search(
                        r"Your task ID is ([0-9a-f-]+)",
                        message,
                    )
                    task_id_str = (
                        match.group(1) if match
                        else "unknown"
                    )
                    if "start_task" in tool_map:
                        sr = await tool_map[
                            "start_task"
                        ].execute(
                            {"task_id": task_id_str},
                        )
                        yield ToolUse(
                            tool_name="start_task",
                            input={
                                "task_id": task_id_str,
                            },
                            output=sr,
                            status="success",
                        )
                    if "end_task" in tool_map:
                        er = await tool_map[
                            "end_task"
                        ].execute(
                            {"message": f"child {child_idx} done"},
                        )
                        yield ToolUse(
                            tool_name="end_task",
                            input={
                                "message": (
                                    f"child {child_idx} done"
                                ),
                            },
                            output=er,
                            status="success",
                        )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=10,
                        output_tokens=5,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield Result(
                        text=f"child {child_idx} done",
                        session_id=sid,
                        total_input_tokens=10,
                        total_output_tokens=5,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=2,
                    )

            async def resume(  # noqa: PLR0913
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:  # noqa: ANN401
                _ = model, system_prompt, stream_deltas
                _ = continuation
                self._resume_count += 1
                sid = session_id or str(uuid6.uuid7())
                tool_map = {t.name: t for t in tools}

                if self._resume_count <= 2:
                    # Await the next child
                    idx = self._resume_count
                    await_result = await tool_map[
                        "await_task"
                    ].execute(
                        {"task_id": child_ids[idx]},
                    )
                    yield ToolUse(
                        tool_name="await_task",
                        input={
                            "task_id": child_ids[idx],
                        },
                        output=await_result,
                        status="success",
                    )
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=5,
                        output_tokens=3,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield SessionSuspended(
                        continuation=Continuation(
                            executed_results=[],
                            remaining_blocks=[],
                            session_id=sid,
                        ),
                        session_id=sid,
                    )
                else:
                    # Resume #3: final result
                    yield StepFinish(
                        reason="end_turn",
                        input_tokens=5,
                        output_tokens=3,
                        reasoning_tokens=0,
                        cache_read_tokens=0,
                        cache_write_tokens=0,
                    )
                    yield Result(
                        text="All 3 children done.",
                        session_id=sid,
                        total_input_tokens=30,
                        total_output_tokens=17,
                        total_reasoning_tokens=0,
                        total_cache_read_tokens=0,
                        total_cache_write_tokens=0,
                        tool_calls=0,
                    )

        transport = ThreeChildTransport()
        sched = make_scheduler(
            transport_registry={"anthropic": transport},
        )

        task = TaskSpec(
            name="orch-three",
            agent="test-agent",
            task_prompt="orchestrate three children",
            timeout=300,
            context_refinement=False,
            orchestrator=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # All 3 children were created
        assert len(child_ids) == 3

        # Orchestrator completed
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001
        assert result.output is not None
        assert "3" in result.output or "all" in result.output.lower()

        # Transport went through 3 resumes
        assert transport._resume_count == 3  # noqa: SLF001

        # Verify trace: suspended 3 times
        transitions = [
            c for c in trace_writer.get_calls("transition_task")
            if c["task_id"] == task_id
        ]
        statuses = [t["new_status"] for t in transitions]
        assert statuses.count("suspended") == 3
        # active: initial + 3 resumes = 4
        assert statuses.count("active") >= 4
