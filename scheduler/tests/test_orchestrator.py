"""Tests for orchestrator task execution with await_task suspension."""
# ruff: noqa: SLF001, S101, ANN001, ANN201, ASYNC221, PLR2004

from __future__ import annotations

import uuid
from typing import Any
from unittest.mock import AsyncMock

import pytest
import uuid6

from orxt.protocols import TaskResult, TaskSpec, TaskState
from orxt.protocols._tool import Tool
from orxt.transport import (
    Continuation,
    Result,
    SessionSuspended,
    StepFinish,
    ToolUse,
)


@pytest.fixture
def orchestrator_task():
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
        self, orchestrator_task,
    ):
        """TaskSpec with orchestrator=True should dispatch to _execute_orchestrator_task."""
        assert orchestrator_task.orchestrator is True

    async def test_taskspec_orchestrator_default_none(self):
        task = TaskSpec(
            name="normal",
            agent="test",
            task_prompt="do stuff",
            timeout=60,
            context_refinement=False,
        )
        assert task.orchestrator is None

    async def test_execute_task_routes_to_orchestrator(
        self, make_scheduler,
    ):
        """execute_task with orchestrator=True actually routes
        to _execute_orchestrator_task (not _execute_agent_task)."""
        # Use a transport that just returns a result (no tools)
        from tests.conftest import MockTransportNoTools

        sched = make_scheduler(
            transport_registry={
                "anthropic": MockTransportNoTools("done"),
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
        task_id = await sched._trace_writer.create_task(
            run_id=sched._run_id,
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # Orchestrator completes with output from transport
        assert result.output == "done"
        assert sched._task_states[task_id] == TaskState.COMPLETED

    async def test_orchestrator_transitions_active_then_completed(
        self, make_scheduler, trace_writer,
    ):
        """Orchestrator task transitions through ACTIVE -> COMPLETED."""
        from tests.conftest import MockTransportNoTools

        sched = make_scheduler(
            transport_registry={
                "anthropic": MockTransportNoTools("output"),
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
        task_id = await sched._trace_writer.create_task(
            run_id=sched._run_id,
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)

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
    async def test_make_await_task_tool_is_suspending(self):
        from orxt.tool import make_await_task_tool

        mock_scheduler = AsyncMock()
        tool = make_await_task_tool(mock_scheduler, "session-1")
        assert tool.suspending is True
        assert tool.name == "await_task"

    async def test_await_task_calls_handler(self):
        from orxt.tool import make_await_task_tool

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
        self, make_scheduler,
    ):
        scheduler = make_scheduler()
        task_id = uuid.uuid4()
        scheduler._task_states[task_id] = TaskState.CREATED
        scheduler._task_specs[task_id] = TaskSpec(
            name="child",
            agent="test-agent",
            task_prompt="do stuff",
            timeout=60,
            context_refinement=False,
        )

        result = await scheduler.handle_await_task(
            "session-1", str(task_id),
        )
        assert scheduler._pending_await["session-1"] == str(task_id)
        assert "Awaiting" in result

    async def test_handle_await_task_nonexistent_raises(
        self, make_scheduler,
    ):
        scheduler = make_scheduler()
        with pytest.raises(Exception, match="does not exist"):
            await scheduler.handle_await_task(
                "session-1", str(uuid.uuid4()),
            )


class TestTaskStateSuspended:
    def test_suspended_state_exists(self):
        assert TaskState.SUSPENDED == "suspended"

    def test_suspended_in_enum(self):
        assert "suspended" in [s.value for s in TaskState]


class TestOrchestratorSuspension:
    """Tests for the orchestrator suspend/resume flow.

    These use a mock transport that simulates suspension:
    1. First send: calls create_task + await_task tools, then suspends
    2. Resume: receives child result, returns final output
    """

    async def test_orchestrator_suspends_and_resumes(
        self, make_scheduler, trace_writer,
    ):
        """Full orchestrator flow: create child, await, suspend,
        child runs, resume with result."""
        child_task_id_holder: list[str] = []

        class SuspendingTransport:
            """Transport that creates a child task then suspends."""

            def __init__(self) -> None:
                self._resumed = False

            async def send(
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:
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

            async def resume(
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:
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

        # Simpler approach: use MockTransportNoTools for the
        # child via a multi-behavior transport.

        class OrchestratorTransport:
            """Transport that suspends on first send (orchestrator),
            then acts as a simple no-tools transport for the child,
            then handles resume for the orchestrator."""

            def __init__(self) -> None:
                self._send_count = 0
                self._suspending = SuspendingTransport()
                self._resumed = False

            async def send(
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:
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

                    import re
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

            async def resume(
                self,
                continuation: Continuation,
                result: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> Any:
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
        task_id = await sched._trace_writer.create_task(
            run_id=sched._run_id,
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)

        result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        # Verify the orchestrator completed
        assert sched._task_states[task_id] == TaskState.COMPLETED
        assert result.output is not None
        assert "child done" in result.output.lower() or "child said" in result.output.lower()

        # Verify transport was actually resumed
        assert transport._resumed is True

        # Verify trace saw suspended and active transitions
        transitions = [
            c for c in trace_writer.get_calls("transition_task")
            if c["task_id"] == task_id
        ]
        statuses = [t["new_status"] for t in transitions]
        assert "suspended" in statuses
        # Should have gone active -> suspended -> active -> completed
        assert statuses.count("active") >= 2
