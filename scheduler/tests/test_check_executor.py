from __future__ import annotations

import inspect
import json
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxtra.protocols._checks import CheckContext
from orxtra.protocols._execution import (
    AgentExecution,
    ScriptExecution,
    Severity,
)
from orxtra.protocols._task import TaskSpec, WorkflowExecution
from orxtra.scheduler._executor import Scheduler
from orxtra.transport import Result, StepFinish

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    from orxtra.protocols._tool import Tool
    from orxtra.transport import Event


class TestRunConsult:
    """Tests for Scheduler.run_consult."""

    async def test_returns_text(
        self, scheduler: Scheduler,
    ) -> None:
        """run_consult delegates to transport and returns text response."""
        result = await scheduler.run_consult(
            "test-agent", "What is 2+2?",
        )
        assert result == "Mock response"

    async def test_unknown_agent_raises(
        self, scheduler: Scheduler,
    ) -> None:
        """run_consult raises ValueError for unknown agent."""
        with pytest.raises(
            ValueError, match="Agent 'nonexistent' not found",
        ):
            await scheduler.run_consult(
                "nonexistent", "question",
            )

    async def test_variable_substitution(
        self, scheduler: Scheduler,
    ) -> None:
        """run_consult substitutes {key} templates in the question."""

        class CapturingTransport:
            def __init__(self) -> None:
                self.received_message: str | None = None

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
            ) -> AsyncIterator[Event]:
                _ = model, system_prompt, tools
                self.received_message = message
                sid = session_id or str(uuid6.uuid7())
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=1, output_tokens=1,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="captured",
                    session_id=sid,
                    total_input_tokens=1,
                    total_output_tokens=1,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        spy = CapturingTransport()
        scheduler._transport_registry["anthropic"] = spy  # noqa: SLF001
        result = await scheduler.run_consult(
            "test-agent",
            "Check {task_name} output: {agent_output}",
            variable_values={
                "task_name": "deploy",
                "agent_output": "success",
            },
        )
        assert result == "captured"
        assert spy.received_message == (
            "Check deploy output: success"
        )

    async def test_no_tools_passed(
        self, scheduler: Scheduler,
    ) -> None:
        """run_consult passes tools=[] to transport (read-only)."""

        class SpyTransport:
            def __init__(self) -> None:
                self.received_tools: list[Tool] | None = None

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
            ) -> AsyncIterator[Event]:
                _ = message, model, system_prompt
                self.received_tools = tools
                sid = session_id or str(uuid6.uuid7())
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=1, output_tokens=1,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="spy",
                    session_id=sid,
                    total_input_tokens=1,
                    total_output_tokens=1,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        spy = SpyTransport()
        scheduler._transport_registry["anthropic"] = spy  # noqa: SLF001
        await scheduler.run_consult("test-agent", "question")
        assert spy.received_tools == []

    async def test_missing_category_raises(self, tmp_path: Path) -> None:
        """run_consult raises ValueError when the agent's category is missing."""
        from orxtra.agent import Agent  # noqa: PLC0415

        agent = Agent(
            name="special-agent",
            description="agent with unknown category",
            prompt="You are special.",
            category="premium",
            allow=["read"],
        )

        sched = Scheduler(
            trace_writer=MockTraceWriter(),  # type: ignore[arg-type]
            transport_registry={"anthropic": MockTransport(auto_execute_tools=True)},  # type: ignore[dict-item]
            agents={"special-agent": agent},
            categories={"default": "anthropic/claude-sonnet-4-6"},
            run_id=uuid6.uuid7(),
            read_root=tmp_path,
            autonomy_level="max",
        )

        with pytest.raises(
            ValueError, match="Category 'premium' not found",
        ):
            await sched.run_consult(
                "special-agent", "question",
            )

    async def test_missing_provider_raises(self, tmp_path: Path) -> None:
        """run_consult raises ValueError when the provider is not registered."""
        sched = Scheduler(
            trace_writer=MockTraceWriter(),  # type: ignore[arg-type]
            transport_registry={},  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories={"default": "anthropic/claude-sonnet-4-6"},
            run_id=uuid6.uuid7(),
            read_root=tmp_path,
            autonomy_level="max",
        )

        with pytest.raises(
            ValueError,
            match="Transport for provider 'anthropic' not found",
        ):
            await sched.run_consult("test-agent", "question")

    async def test_system_prompt_from_agent(
        self, scheduler: Scheduler,
    ) -> None:
        """run_consult passes the agent's prompt as system_prompt."""

        class PromptCapture:
            def __init__(self) -> None:
                self.received_system_prompt: str | None = None

            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
            ) -> AsyncIterator[Event]:
                _ = message, model, tools
                self.received_system_prompt = system_prompt
                sid = session_id or str(uuid6.uuid7())
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=1, output_tokens=1,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="ok",
                    session_id=sid,
                    total_input_tokens=1,
                    total_output_tokens=1,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        spy = PromptCapture()
        scheduler._transport_registry["anthropic"] = spy  # noqa: SLF001
        await scheduler.run_consult("test-agent", "question")
        assert spy.received_system_prompt == "You are a test agent."


class TestRunWorkflowCheck:
    """Tests for Scheduler.run_workflow_check."""

    async def test_empty_tasks_passes(
        self, scheduler: Scheduler,
    ) -> None:
        """Workflow with no tasks returns CheckResult(passed=True)."""
        execution = WorkflowExecution(
            name="empty_check",
            description="Empty workflow",
            tasks=[],
            postchecks=[],
        )
        result = await scheduler.run_workflow_check(execution)
        assert result.passed is True
        assert "no tasks" in result.message.lower()

    async def test_passing_workflow(
        self, scheduler: Scheduler,
    ) -> None:
        """Workflow with a passing subtask returns passed=True."""
        execution = WorkflowExecution(
            name="pass_check",
            description="Passing workflow check",
            tasks=[
                TaskSpec(
                    name="sub1", decision_point=True,
                ),
            ],
            postchecks=[],
        )
        result = await scheduler.run_workflow_check(execution)
        assert result.passed is True
        assert "pass_check" in result.message

    async def test_non_workflow_execution_fails(
        self, scheduler: Scheduler,
    ) -> None:
        """Passing a non-WorkflowExecution returns passed=False."""
        script = ScriptExecution(
            callable="some.module:check",
        )
        result = await scheduler.run_workflow_check(
            script,  # type: ignore[arg-type]
        )
        assert result.passed is False
        assert "Expected WorkflowExecution" in result.message


class TestCheckExecutorProtocol:
    """Tests verifying Scheduler satisfies CheckExecutor."""

    async def test_protocol_methods_exist(self) -> None:
        """Scheduler has the methods required by CheckExecutor."""
        assert hasattr(Scheduler, "run_consult")
        assert hasattr(Scheduler, "run_workflow_check")

    async def test_run_consult_signature(self) -> None:
        """run_consult has the correct parameter names."""
        sig = inspect.signature(Scheduler.run_consult)
        params = list(sig.parameters.keys())
        assert "agent" in params
        assert "question" in params
        assert "variable_values" in params

    async def test_run_workflow_check_signature(self) -> None:
        """run_workflow_check has the correct parameter names."""
        sig = inspect.signature(
            Scheduler.run_workflow_check,
        )
        params = list(sig.parameters.keys())
        assert "execution" in params


class TestCheckExecutorIntegration:
    """Integration tests: verify module calling Scheduler."""

    async def test_agent_execution_invalid_verdict(
        self, scheduler: Scheduler,
    ) -> None:
        """AgentExecution postcheck with invalid JSON returns passed=False."""
        from orxtra.verify._execution import _run_agent  # noqa: PLC0415

        agent_exec = AgentExecution(
            agent="test-agent",
            task="Review the output",
            block_threshold=Severity.MAJOR,
        )

        ctx = CheckContext(
            variables={},
            agent_output="some output",
            run_id=scheduler._run_id,  # noqa: SLF001
            session_id=None,
            task_name="test_task",
            task_id=uuid6.uuid7(),
            attempt=1,
            parent_task_id=None,
        )

        # MockTransport returns "Mock response" -- not valid
        # CheckVerdict JSON, so _run_agent returns passed=False
        result = await _run_agent(
            agent_exec, ctx, scheduler, None,
        )
        assert result.passed is False
        assert "invalid verdict" in result.message.lower()

    async def test_agent_execution_valid_verdict(self, tmp_path: Path) -> None:
        """AgentExecution postcheck with valid verdict JSON returns passed=True."""
        from orxtra.verify._execution import _run_agent  # noqa: PLC0415

        verdict_json = json.dumps({
            "verdict": "pass",
            "issues": [],
            "criteria_review": [
                {
                    "criterion": "correctness",
                    "met": True,
                    "evidence": "looks good",
                },
            ],
            "summary": "All checks passed",
        })

        class VerdictTransport:
            async def send(  # noqa: PLR0913
                self,
                message: str,
                *,
                model: str,
                system_prompt: str,
                tools: list[Tool],
                session_id: str | None = None,
            ) -> AsyncIterator[Event]:
                _ = message, model, system_prompt, tools
                sid = session_id or str(uuid6.uuid7())
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=1, output_tokens=1,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text=verdict_json,
                    session_id=sid,
                    total_input_tokens=1,
                    total_output_tokens=1,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=0,
                )

        sched = Scheduler(
            trace_writer=MockTraceWriter(),  # type: ignore[arg-type]
            transport_registry={
                "anthropic": VerdictTransport(),
            },  # type: ignore[dict-item]
            agents={"test-agent": make_agent()},
            categories=make_categories(),
            run_id=uuid6.uuid7(),
            read_root=tmp_path,
            autonomy_level="max",
        )

        agent_exec = AgentExecution(
            agent="test-agent",
            task="Review the output",
            block_threshold=Severity.MAJOR,
        )

        ctx = CheckContext(
            variables={},
            agent_output="task completed successfully",
            run_id=sched._run_id,  # noqa: SLF001
            session_id=None,
            task_name="test_task",
            task_id=uuid6.uuid7(),
            attempt=1,
            parent_task_id=None,
        )

        result = await _run_agent(
            agent_exec, ctx, sched, None,
        )
        assert result.passed is True
        assert "All checks passed" in result.message
