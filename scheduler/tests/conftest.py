from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from orxt.agent import Agent
from orxt.protocols._tool import Tool, ToolError
from orxt.scheduler._executor import Scheduler
from orxt.transport import Result, StepFinish, ToolUse

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator
    from decimal import Decimal

    from orxt.transport import Event


class MockTraceWriter:
    """Records all calls for verification."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self._run_statuses: dict[uuid.UUID, str] = {}
        self._task_statuses: dict[uuid.UUID, str] = {}
        self._event_callback: Any = None
        self._control_callback: Any = None

    def _record(
        self, method: str, **kwargs: object,
    ) -> None:
        self.calls.append((method, dict(kwargs)))

    async def create_run(
        self,
        intent: str,
        config: dict[str, Any],
        autonomy_level: str,
    ) -> uuid.UUID:
        run_id = uuid6.uuid7()
        self._record(
            "create_run",
            intent=intent,
            config=config,
            autonomy_level=autonomy_level,
        )
        self._run_statuses[run_id] = "running"
        return run_id

    async def transition_run(
        self,
        run_id: uuid.UUID,
        new_status: str,
        reason: str | None = None,
    ) -> None:
        self._record(
            "transition_run",
            run_id=run_id,
            new_status=new_status,
            reason=reason,
        )
        self._run_statuses[run_id] = new_status

    async def create_task(
        self,
        run_id: uuid.UUID,
        parent_task_id: uuid.UUID | None,
        name: str,
        task_type: str,
        config: dict[str, Any] | None = None,
    ) -> uuid.UUID:
        task_id = uuid6.uuid7()
        self._record(
            "create_task",
            run_id=run_id,
            parent_task_id=parent_task_id,
            name=name,
            task_type=task_type,
            config=config,
        )
        self._task_statuses[task_id] = "created"
        return task_id

    async def transition_task(
        self,
        task_id: uuid.UUID,
        new_status: str,
        reason: str | None = None,
    ) -> None:
        self._record(
            "transition_task",
            task_id=task_id,
            new_status=new_status,
            reason=reason,
        )
        self._task_statuses[task_id] = new_status

    async def create_task_attempt(
        self, task_id: uuid.UUID, attempt: int,
    ) -> uuid.UUID:
        attempt_id = uuid6.uuid7()
        self._record(
            "create_task_attempt",
            task_id=task_id,
            attempt=attempt,
        )
        return attempt_id

    async def complete_task_attempt(  # noqa: PLR0913
        self,
        attempt_id: uuid.UUID,
        agent_output: str,
        structured_output: dict[str, Any] | None,
        check_result: dict[str, Any] | None,
        check_verdict: str | None,
        session_id: uuid.UUID | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: Decimal,
        duration_seconds: float,
    ) -> None:
        self._record(
            "complete_task_attempt",
            attempt_id=attempt_id,
            agent_output=agent_output,
            check_verdict=check_verdict,
            cost_usd=cost_usd,
            duration_seconds=duration_seconds,
        )

    async def fail_task_attempt(  # noqa: PLR0913
        self,
        attempt_id: uuid.UUID,
        error: str,
        session_id: uuid.UUID | None,
        input_tokens: int,
        output_tokens: int,
        reasoning_tokens: int,
        cache_read_tokens: int,
        cache_write_tokens: int,
        cost_usd: Decimal,
        duration_seconds: float,
    ) -> None:
        self._record(
            "fail_task_attempt",
            attempt_id=attempt_id,
            error=error,
        )

    async def write_event(
        self,
        run_id: uuid.UUID,
        event_type: str,
        data: dict[str, Any],
        task_id: uuid.UUID | None = None,
    ) -> uuid.UUID:
        event_id = uuid6.uuid7()
        self._record(
            "write_event",
            run_id=run_id,
            event_type=event_type,
            data=data,
        )
        return event_id

    async def write_transcript_entry(  # noqa: PLR0913
        self,
        session_id: uuid.UUID,
        run_id: uuid.UUID,
        turn: int,
        role: str,
        content: str,
        tool_calls: dict[str, Any] | None = None,
        tokens: dict[str, Any] | None = None,
    ) -> None:
        self._record(
            "write_transcript_entry",
            session_id=session_id,
            role=role,
            content=content,
        )

    async def write_coherence_summary(
        self,
        run_id: uuid.UUID,
        summary: str,
    ) -> None:
        self._record(
            "write_coherence_summary",
            run_id=run_id,
            summary=summary,
        )

    async def write_lesson(
        self, **kwargs: object,
    ) -> None:
        self._record("write_lesson", **kwargs)

    async def write_constraint(
        self, **kwargs: object,
    ) -> None:
        self._record("write_constraint", **kwargs)

    async def subscribe_run_control(
        self,
        run_id: uuid.UUID,
        callback: Any,  # noqa: ANN401
    ) -> None:
        self._control_callback = callback
        self._record(
            "subscribe_run_control",
            run_id=run_id,
        )

    async def unsubscribe_run_control(
        self,
        run_id: uuid.UUID,
    ) -> None:
        self._control_callback = None
        self._record(
            "unsubscribe_run_control",
            run_id=run_id,
        )

    def get_calls(self, method: str) -> list[dict[str, Any]]:
        return [
            kwargs for m, kwargs in self.calls if m == method
        ]


class MockTransport:
    """Simulates an LLM that calls start_task then end_task.

    The agent behavior:
    1. LLM receives prompt with task ID
    2. LLM calls start_task tool
    3. LLM calls end_task tool with message
    4. LLM returns final text

    This mock simulates this by executing the tools
    that are passed in via the tools parameter.
    """

    def __init__(
        self,
        response_text: str = "Mock response",
    ) -> None:
        self._response_text = response_text
        self._call_count = 0

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
        _ = message, model, system_prompt
        _ = stream_deltas
        self._call_count += 1
        sid = session_id or str(uuid6.uuid7())

        tool_map = {t.name: t for t in tools}

        # Extract task_id from prompt message
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
                start_result = await tool_map[
                    "start_task"
                ].execute({"task_id": task_id_str})
            except ToolError as e:
                start_result = f"Error: {e}"
            yield ToolUse(
                tool_name="start_task",
                input={"task_id": task_id_str},
                output=start_result,
                status="success",
            )

        if "end_task" in tool_map:
            try:
                end_result = await tool_map[
                    "end_task"
                ].execute(
                    {"message": self._response_text},
                )
            except ToolError as e:
                end_result = f"Error: {e}"
            yield ToolUse(
                tool_name="end_task",
                input={
                    "message": self._response_text,
                },
                output=end_result,
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
            text=self._response_text,
            session_id=sid,
            total_input_tokens=10,
            total_output_tokens=5,
            total_reasoning_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            tool_calls=2,
        )


class MockTransportNoTools:
    """Transport that does not call any tools.

    For non-agent tasks or tasks where the agent
    never calls start_task/end_task.
    """

    def __init__(
        self,
        response_text: str = "Mock response",
    ) -> None:
        self._response_text = response_text

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
        _ = message, model, system_prompt, tools
        _ = stream_deltas
        sid = session_id or str(uuid6.uuid7())
        yield StepFinish(
            reason="end_turn",
            input_tokens=10,
            output_tokens=5,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        yield Result(
            text=self._response_text,
            session_id=sid,
            total_input_tokens=10,
            total_output_tokens=5,
            total_reasoning_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            tool_calls=0,
        )


def make_agent(
    name: str = "test-agent",
    category: str = "default",
) -> Agent:
    return Agent(
        name=name,
        description="A test agent",
        prompt="You are a test agent.",
        category=category,
        allow=["read"],
    )


def make_categories() -> dict[str, str]:
    return {"default": "anthropic/claude-sonnet-4-6"}


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def transport() -> MockTransport:
    return MockTransport()


@pytest.fixture
def agents() -> dict[str, Agent]:
    return {"test-agent": make_agent()}


@pytest.fixture
def categories() -> dict[str, str]:
    return make_categories()


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid6.uuid7()


@pytest.fixture
def scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents,
        categories=categories,
        run_id=run_id,
    )
