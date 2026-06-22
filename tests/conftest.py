from __future__ import annotations

import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.protocols._task import TaskSpec
from orxtra.protocols._tool import Tool, ToolError
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._types import WorkflowConfig
from orxtra.transport import Result, StepFinish, ToolUse

if TYPE_CHECKING:
    import uuid
    from collections.abc import AsyncIterator

    from orxtra.transport import Event


from tests.shared_mocks import MockTraceWriter

pytest_plugins = ["tests.pg_fixtures"]

# ---------------------------------------------------------------------------
# AgentTurn -- defines what tool calls an agent makes in a single turn
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AgentTurn:
    """Defines what tool calls an agent makes in a single conversation turn.

    Each AgentTurn corresponds to one call to Transport.send(). The transport
    executes the listed tool calls using the real tool.execute() method, then
    yields the text_response as the final Result.
    """

    tool_calls: list[tuple[str, dict[str, Any]]]
    text_response: str = "Done"


def _extract_task_id(message: str) -> str | None:
    """Extract the task_id from the scheduler's prompt prefix."""
    match = re.search(r"Your task ID is ([0-9a-f-]+)", message)
    return match.group(1) if match else None


# ---------------------------------------------------------------------------
# IntegrationMockTransport -- single-agent mock with real tool execution
# ---------------------------------------------------------------------------


class IntegrationMockTransport:
    """Mock transport that simulates LLM behavior with real tool execution.

    Accepts a list of AgentTurns. Each call to send() consumes one turn,
    executes its tool calls using the actual tool.execute() methods (which
    call into the Scheduler's lifecycle tools), and yields transport events.
    """

    def __init__(self, turns: list[AgentTurn]) -> None:
        self._turns = list(turns)
        self._turn_index = 0
        self.send_calls: list[dict[str, Any]] = []

    async def send(  # noqa: PLR0913
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
    ) -> AsyncIterator[Event]:
        self.send_calls.append({
            "message": message,
            "model": model,
            "system_prompt": system_prompt,
            "tool_count": len(tools),
            "session_id": session_id,
        })

        task_id_str = _extract_task_id(message)
        sid = session_id or str(uuid6.uuid7())

        if self._turn_index >= len(self._turns):
            yield StepFinish(
                reason="end_turn",
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
            )
            yield Result(
                text="",
                session_id=sid,
                total_input_tokens=0,
                total_output_tokens=0,
                total_reasoning_tokens=0,
                total_cache_read_tokens=0,
                total_cache_write_tokens=0,
                tool_calls=0,
            )
            return

        turn = self._turns[self._turn_index]
        self._turn_index += 1

        tool_map = {t.name: t for t in tools}
        executed_count = 0

        for tool_name, raw_tool_args in turn.tool_calls:
            # Auto-inject task_id for start_task when not provided
            needs_task_id = (
                tool_name == "start_task"
                and "task_id" not in raw_tool_args
                and task_id_str
            )
            if needs_task_id:
                tool_args = {**raw_tool_args, "task_id": task_id_str}
            else:
                tool_args = raw_tool_args

            tool = tool_map.get(tool_name)
            if tool is None:
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output="",
                    status="error",
                    error=f"Unknown tool: {tool_name}",
                )
                executed_count += 1
                continue
            try:
                tool_output = await tool.execute(tool_args)
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output=tool_output.text,
                    status="success",
                )
            except ToolError as e:
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output="",
                    status="error",
                    error=str(e),
                )
            except Exception as e:  # noqa: BLE001
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output="",
                    status="error",
                    error=f"{type(e).__name__}: {e}",
                )
            executed_count += 1

        yield StepFinish(
            reason="end_turn",
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        yield Result(
            text=turn.text_response,
            session_id=sid,
            total_input_tokens=100,
            total_output_tokens=50,
            total_reasoning_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            tool_calls=executed_count,
        )

    @property
    def turns_remaining(self) -> int:
        return max(0, len(self._turns) - self._turn_index)

    @property
    def turns_consumed(self) -> int:
        return self._turn_index


# ---------------------------------------------------------------------------
# MultiAgentMockTransport -- maps agent prompts to turn sequences
# ---------------------------------------------------------------------------


class MultiAgentMockTransport:
    """Mock transport for workflows with multiple agent tasks.

    Routes each send() call to the correct turn sequence by matching the
    system_prompt against registered substrings. This allows different
    agents in a workflow to have independent scripted behaviors.
    """

    def __init__(
        self,
        agent_turns: dict[str, list[AgentTurn]],
    ) -> None:
        """Initialize with agent turn mappings.

        Args:
            agent_turns: Maps agent system_prompt substrings to their
                turn sequences. On each send(), the system_prompt is
                matched against these keys (first match wins).
        """
        self._agent_turns = agent_turns
        self._agent_indices: dict[str, int] = dict.fromkeys(agent_turns, 0)
        self.send_calls: list[dict[str, Any]] = []

    def _match_agent(self, system_prompt: str) -> str | None:
        """Find the agent key matching this system_prompt."""
        for key in self._agent_turns:
            if key in system_prompt:
                return key
        return None

    async def send(  # noqa: PLR0913
        self,
        message: str,
        *,
        model: str,
        system_prompt: str,
        tools: list[Tool],
        session_id: str | None = None,
    ) -> AsyncIterator[Event]:
        self.send_calls.append({
            "message": message,
            "model": model,
            "system_prompt": system_prompt,
            "tool_count": len(tools),
            "session_id": session_id,
        })

        task_id_str = _extract_task_id(message)
        sid = session_id or str(uuid6.uuid7())
        agent_key = self._match_agent(system_prompt)

        if agent_key is None or self._agent_indices[agent_key] >= len(
            self._agent_turns[agent_key],
        ):
            yield StepFinish(
                reason="end_turn",
                input_tokens=0,
                output_tokens=0,
                reasoning_tokens=0,
                cache_read_tokens=0,
                cache_write_tokens=0,
            )
            yield Result(
                text="",
                session_id=sid,
                total_input_tokens=0,
                total_output_tokens=0,
                total_reasoning_tokens=0,
                total_cache_read_tokens=0,
                total_cache_write_tokens=0,
                tool_calls=0,
            )
            return

        turn_index = self._agent_indices[agent_key]
        turn = self._agent_turns[agent_key][turn_index]
        self._agent_indices[agent_key] = turn_index + 1

        tool_map = {t.name: t for t in tools}
        executed_count = 0

        for tool_name, raw_tool_args in turn.tool_calls:
            # Auto-inject task_id for start_task when not provided
            needs_task_id = (
                tool_name == "start_task"
                and "task_id" not in raw_tool_args
                and task_id_str
            )
            if needs_task_id:
                tool_args = {**raw_tool_args, "task_id": task_id_str}
            else:
                tool_args = raw_tool_args

            tool = tool_map.get(tool_name)
            if tool is None:
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output="",
                    status="error",
                    error=f"Unknown tool: {tool_name}",
                )
                executed_count += 1
                continue
            try:
                tool_output = await tool.execute(tool_args)
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output=tool_output.text,
                    status="success",
                )
            except ToolError as e:
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output="",
                    status="error",
                    error=str(e),
                )
            except Exception as e:  # noqa: BLE001
                yield ToolUse(
                    tool_name=tool_name,
                    input=tool_args,
                    output="",
                    status="error",
                    error=f"{type(e).__name__}: {e}",
                )
            executed_count += 1

        yield StepFinish(
            reason="end_turn",
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
        )
        yield Result(
            text=turn.text_response,
            session_id=sid,
            total_input_tokens=100,
            total_output_tokens=50,
            total_reasoning_tokens=0,
            total_cache_read_tokens=0,
            total_cache_write_tokens=0,
            tool_calls=executed_count,
        )

    def turns_remaining(self, agent_key: str) -> int:
        if agent_key not in self._agent_turns:
            return 0
        return max(
            0,
            len(self._agent_turns[agent_key])
            - self._agent_indices.get(agent_key, 0),
        )


# ---------------------------------------------------------------------------
# Helper functions
# ---------------------------------------------------------------------------


def make_agent(
    name: str = "test-agent",
    category: str = "default",
    allow: list[str] | None = None,
) -> Agent:
    """Create an Agent for testing."""
    return Agent(
        name=name,
        description=f"Test agent: {name}",
        prompt=f"You are {name}.",
        category=category,
        allow=allow if allow is not None else ["read"],
    )


def make_categories() -> dict[str, str]:
    """Standard category mapping for tests."""
    return {"default": "anthropic/claude-sonnet-4-6"}


def simple_task(
    name: str = "t1",
    agent: str = "test-agent",
    timeout: int = 60,
    **kwargs: Any,  # noqa: ANN401
) -> TaskSpec:
    """Create a simple agent TaskSpec for testing."""
    return TaskSpec(
        name=name,
        agent=agent,
        task_prompt=kwargs.pop("task_prompt", f"Do {name}"),
        timeout=timeout,
        context_refinement=kwargs.pop("context_refinement", False),
        **kwargs,
    )


def simple_workflow(
    tasks: list[TaskSpec] | None = None,
    dependencies: dict[str, list[str]] | None = None,
) -> WorkflowConfig:
    """Create a simple WorkflowConfig for testing."""
    return WorkflowConfig(
        name="test-workflow",
        description="A test workflow",
        tasks=tasks if tasks is not None else [simple_task()],
        dependencies=dependencies if dependencies is not None else {},
    )


def make_scheduler(  # noqa: PLR0913
    trace_writer: MockTraceWriter,
    transport: IntegrationMockTransport | MultiAgentMockTransport,
    run_id: uuid.UUID,
    agents: dict[str, Agent] | None = None,
    categories: dict[str, str] | None = None,
    read_root: Path | None = None,
) -> Scheduler:
    """Create a Scheduler with standard test wiring.

    The transport is registered under the "anthropic" provider key,
    matching the default category's "anthropic/claude-sonnet-4-6" model.
    """
    if agents is None:
        agents = {"test-agent": make_agent()}
    if categories is None:
        categories = make_categories()
    if read_root is None:
        read_root = Path(tempfile.mkdtemp())
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents,
        categories=categories,
        run_id=run_id,
        read_root=read_root,
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def trace_writer() -> MockTraceWriter:
    return MockTraceWriter()


@pytest.fixture
def run_id() -> uuid.UUID:
    return uuid6.uuid7()
