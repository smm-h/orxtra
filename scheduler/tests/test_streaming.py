"""Tests for stream_deltas plumbing from TaskSpec to transport."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

import uuid6
from orxt.agent import Agent
from orxt.protocols import TaskSpec, TaskState
from orxt.protocols._tool import Tool, ToolError
from orxt.scheduler._executor import Scheduler
from orxt.transport import Result, StepFinish, StreamDelta, ToolUse

from tests.conftest import (
    MockTraceWriter,
    make_categories,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from orxt.transport import Event


class StreamingTransport:
    """Transport that emits StreamDelta events when stream_deltas=True."""

    def __init__(self) -> None:
        self.last_stream_deltas: bool | None = None

    async def send(  # noqa: PLR0913
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
        stream_deltas: bool = False,
    ) -> AsyncIterator[Event]:
        _ = model, system_prompt
        self.last_stream_deltas = stream_deltas
        sid = session_id or str(uuid6.uuid7())
        tool_map = {t.name: t for t in tools}

        task_id_match = re.search(
            r"Your task ID is ([0-9a-f-]+)", message,
        )
        task_id_str = (
            task_id_match.group(1)
            if task_id_match
            else "unknown"
        )

        if "start_task" in tool_map:
            try:
                sr = await tool_map["start_task"].execute(
                    {"task_id": task_id_str},
                )
            except ToolError as e:
                sr = f"Error: {e}"
            yield ToolUse(
                tool_name="start_task",
                input={"task_id": task_id_str},
                output=sr,
                status="success",
            )

        # Emit StreamDelta events if streaming is enabled
        if stream_deltas:
            yield StreamDelta(text="chunk1")
            yield StreamDelta(text="chunk2")

        if "end_task" in tool_map:
            try:
                er = await tool_map["end_task"].execute(
                    {"message": "streaming done"},
                )
            except ToolError as e:
                er = f"Error: {e}"
            yield ToolUse(
                tool_name="end_task",
                input={"message": "streaming done"},
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
            text="streaming done",
            session_id=sid,
            total_input_tokens=10,
            total_output_tokens=5,
            total_reasoning_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            tool_calls=2,
        )


def _make_agent() -> Agent:
    return Agent(
        name="test-agent",
        description="A test agent",
        prompt="You are a test agent.",
        category="default",
        allow=["read"],
    )


def _make_scheduler(
    transport: StreamingTransport,
    tmp_path: Path,
) -> Scheduler:
    trace = MockTraceWriter()
    return Scheduler(
        trace_writer=trace,
        transport_registry={"anthropic": transport},
        agents={"test-agent": _make_agent()},
        categories=make_categories(),
        run_id=uuid6.uuid7(),
        read_root=tmp_path,
    )


class TestStreamDeltasFlag:
    """TaskSpec.stream_deltas is passed through to transport."""

    async def test_stream_deltas_true_reaches_transport(
        self, tmp_path: Path,
    ) -> None:
        transport = StreamingTransport()
        sched = _make_scheduler(transport, tmp_path)

        task = TaskSpec(
            name="streaming-task",
            agent="test-agent",
            task_prompt="do it",
            timeout=60,
            context_refinement=False,
            stream_deltas=True,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        _result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        assert transport.last_stream_deltas is True
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001

    async def test_stream_deltas_false_by_default(
        self, tmp_path: Path,
    ) -> None:
        transport = StreamingTransport()
        sched = _make_scheduler(transport, tmp_path)

        task = TaskSpec(
            name="normal-task",
            agent="test-agent",
            task_prompt="do it",
            timeout=60,
            context_refinement=False,
        )
        task_id = await sched._trace_writer.create_task(  # noqa: SLF001
            run_id=sched._run_id,  # noqa: SLF001
            parent_task_id=None,
            name=task.name,
            task_type="agent",
        )
        sched._init_task_state(task_id, task, parent=None)  # noqa: SLF001

        _result = await sched.execute_task(
            task, None, task_id=task_id,
        )

        assert transport.last_stream_deltas is False
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001


class TestOrchestratorIgnoresDeltas:
    """Orchestrator event loop ignores StreamDelta events."""

    async def test_orchestrator_with_stream_deltas(
        self, tmp_path: Path,
    ) -> None:
        """Orchestrator completes normally even when StreamDelta
        events are in the stream."""

        class OrchestratorStreamTransport:
            """Transport that emits StreamDelta in orchestrator send."""

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
                stream_deltas: bool = False,
            ) -> AsyncIterator[Event]:
                _ = model, system_prompt, stream_deltas
                sid = session_id or str(uuid6.uuid7())

                # Emit deltas that should be ignored
                yield StreamDelta(text="orch-chunk-1")
                yield StreamDelta(text="orch-chunk-2")

                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="orchestrator output",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        transport = OrchestratorStreamTransport()
        trace = MockTraceWriter()
        sched = Scheduler(
            trace_writer=trace,
            transport_registry={"anthropic": transport},
            agents={"test-agent": _make_agent()},
            categories=make_categories(),
            run_id=uuid6.uuid7(),
            read_root=tmp_path,
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

        assert result.output == "orchestrator output"
        assert sched._task_states[task_id] == TaskState.COMPLETED  # noqa: SLF001
