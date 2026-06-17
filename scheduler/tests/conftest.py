from __future__ import annotations

import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from orxt.agent import Agent
from orxt.protocols._tool import Tool, ToolError
from orxt.scheduler._executor import Scheduler
from orxt.transport import Result, StepFinish, ToolUse

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator, Callable

    from orxt.transport import Event

# Import MockTraceWriter from the repo-root tests/shared_mocks.py.
# Direct importlib path import avoids scheduler/tests/ shadowing the
# root tests/ package when pytest runs from the scheduler/ subdirectory.
import importlib.util as _ilu

_spec = _ilu.spec_from_file_location(
    "tests.shared_mocks",
    Path(__file__).resolve().parents[2] / "tests" / "shared_mocks.py",
)
_mod = _ilu.module_from_spec(_spec)  # type: ignore[arg-type]
_spec.loader.exec_module(_mod)  # type: ignore[union-attr]
MockTraceWriter = _mod.MockTraceWriter


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
def scheduler(  # noqa: PLR0913
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents,
        categories=categories,
        run_id=run_id,
        read_root=tmp_path,
    )


@pytest.fixture
def make_scheduler(  # noqa: PLR0913
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    agents: dict[str, Agent],
    categories: dict[str, str],
    run_id: uuid.UUID,
    tmp_path: Path,
) -> Callable[..., Scheduler]:
    """Factory fixture for creating scheduler instances."""

    def _make(**kwargs: Any) -> Scheduler:  # noqa: ANN401
        defaults: dict[str, Any] = {
            "trace_writer": trace_writer,
            "transport_registry": {"anthropic": transport},
            "agents": agents,
            "categories": categories,
            "run_id": run_id,
            "read_root": tmp_path,
        }
        defaults.update(kwargs)
        return Scheduler(**defaults)  # type: ignore[arg-type]

    return _make
