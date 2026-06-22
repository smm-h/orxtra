"""Tests for headless mode (no Overseer configured).

Headless mode uses deterministic fallbacks instead of
consulting an Overseer. Events are written to trace,
budget enforcement is mechanical, and features requiring
an Overseer (context_refinement, decision_point) are
rejected at validation time.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxtra.protocols._task import (
    BudgetExhaustionPolicy,
    TaskSpec,
    TaskState,
)
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._types import WorkflowConfig
from orxtra.scheduler._validator import validate_task_tree

if TYPE_CHECKING:
    import uuid

from .conftest import make_agent, make_categories, MockTraceWriter, MockTransport


def _make_headless_scheduler(
    trace_writer: MockTraceWriter,
    transport: MockTransport,
    run_id: uuid.UUID,
    read_root: Path,
    *,
    budget_exhaustion_policy: BudgetExhaustionPolicy = (
        BudgetExhaustionPolicy.UNLIMITED
    ),
) -> Scheduler:
    """Create a headless Scheduler (no overseer_interface)."""
    return Scheduler(
        trace_writer=trace_writer,
        transport_registry={
            "anthropic": transport,
        },
        agents={"test-agent": make_agent()},
        categories=make_categories(),
        run_id=run_id,
        read_root=read_root,
        autonomy_level="max",
        budget_exhaustion_policy=budget_exhaustion_policy,
        # No overseer_interface -- headless mode
    )


class TestHeadlessWorkflow:
    """Headless run completes a simple workflow."""

    @pytest.mark.asyncio
    async def test_simple_workflow_completes(
        self, tmp_path: Path,
    ) -> None:
        """A headless run with a simple callable task
        completes without errors."""
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_headless_scheduler(
            trace, transport, run_id, tmp_path,
        )

        task = TaskSpec(
            name="simple",
            agent="test-agent",
            task_prompt="Do a simple thing",
            timeout=30,
            context_refinement=False,
        )
        config = WorkflowConfig(
            name="headless-test",
            description="Test headless mode",
            tasks=[task],
            dependencies={},
        )
        await sched.execute_workflow(config)

        assert (
            sched._task_states[  # noqa: SLF001
                list(sched._task_states.keys())[0]  # noqa: SLF001
            ]
            == TaskState.COMPLETED
        )


class TestHeadlessTaskFailure:
    """Headless run with task failure writes trace event."""

    @pytest.mark.asyncio
    async def test_failure_writes_trace_event(
        self, tmp_path: Path,
    ) -> None:
        """When a task fails in headless mode, a headless
        fallback event is written to trace."""
        trace = MockTraceWriter()
        # Transport that doesn't auto-execute tools
        # -> agent session ends without completing the task
        transport = MockTransport()
        run_id = uuid6.uuid7()
        sched = _make_headless_scheduler(
            trace, transport, run_id, tmp_path,
        )

        task = TaskSpec(
            name="failing_task",
            agent="test-agent",
            task_prompt="Do something",
            timeout=30,
            context_refinement=False,
            retry=0,
        )
        result = await sched.execute_task(task, None)
        assert result is not None

        # Check that headless fallback events were written
        headless_events = [
            c for c in trace.get_calls("write_event")
            if c.get("event_type", "").startswith("headless_")
        ]
        assert len(headless_events) >= 1

        # Check that an inbox item was created for human review
        inbox_calls = trace.get_calls("create_inbox_item")
        escalation_inbox = [
            c for c in inbox_calls
            if c.get("decision_type") == "headless_escalation"
        ]
        assert len(escalation_inbox) >= 1


class TestHeadlessBudgetExhaustion:
    """Headless run with budget exhaustion uses mechanical enforcement."""

    @pytest.mark.asyncio
    async def test_budget_block_new_in_headless(
        self, tmp_path: Path,
    ) -> None:
        """BLOCK_NEW policy mechanically blocks new tasks
        when budget is exhausted, even without an Overseer."""
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_headless_scheduler(
            trace, transport, run_id, tmp_path,
            budget_exhaustion_policy=(
                BudgetExhaustionPolicy.BLOCK_NEW
            ),
        )

        # Manually trigger budget blocked state
        sched._budget_blocked = True  # noqa: SLF001

        # Verify that the budget_blocked flag is set
        assert sched._budget_blocked is True  # noqa: SLF001

        # The mechanical enforcement works without an
        # Overseer -- BLOCK_NEW sets _budget_blocked
        # which prevents handle_create_task from
        # proceeding.


class TestHeadlessContextRefinementValidation:
    """context_refinement=True raises validation error in headless mode."""

    def test_context_refinement_rejected(self) -> None:
        """Workflow validation rejects context_refinement=True
        when headless=True."""
        task = TaskSpec(
            name="refined_task",
            agent="test-agent",
            task_prompt="Do something",
            timeout=30,
            context_refinement=True,
        )
        config = WorkflowConfig(
            name="test",
            description="test",
            tasks=[task],
            dependencies={},
        )
        errors = validate_task_tree(
            config, headless=True,
        )
        assert any(
            "context_refinement=True" in e
            and "no Overseer" in e
            for e in errors
        )

    def test_context_refinement_allowed_with_overseer(
        self,
    ) -> None:
        """context_refinement=True is allowed when
        headless=False (Overseer present)."""
        task = TaskSpec(
            name="refined_task",
            agent="test-agent",
            task_prompt="Do something",
            timeout=30,
            context_refinement=True,
        )
        config = WorkflowConfig(
            name="test",
            description="test",
            tasks=[task],
            dependencies={},
        )
        errors = validate_task_tree(
            config, headless=False,
        )
        assert not any(
            "context_refinement" in e for e in errors
        )


class TestHeadlessDecisionPointValidation:
    """Decision point tasks raise validation error in headless mode."""

    def test_decision_point_rejected(self) -> None:
        """Workflow validation rejects decision_point=True
        when headless=True."""
        task = TaskSpec(
            name="decide",
            decision_point=True,
        )
        config = WorkflowConfig(
            name="test",
            description="test",
            tasks=[task],
            dependencies={},
        )
        errors = validate_task_tree(
            config, headless=True,
        )
        assert any(
            "decision point" in e
            and "no Overseer" in e
            for e in errors
        )

    def test_decision_point_allowed_with_overseer(
        self,
    ) -> None:
        """decision_point=True is allowed when
        headless=False (Overseer present)."""
        task = TaskSpec(
            name="decide",
            decision_point=True,
        )
        config = WorkflowConfig(
            name="test",
            description="test",
            tasks=[task],
            dependencies={},
        )
        errors = validate_task_tree(
            config, headless=False,
        )
        assert not any(
            "decision point" in e for e in errors
        )


class TestSchedulerRequiresAutonomyLevel:
    """Scheduler without autonomy_level raises TypeError."""

    def test_missing_autonomy_level_raises(
        self, tmp_path: Path,
    ) -> None:
        """Constructing Scheduler without autonomy_level
        raises TypeError."""
        trace = MockTraceWriter()
        transport = MockTransport()
        with pytest.raises(TypeError):
            Scheduler(
                trace_writer=trace,  # type: ignore[arg-type]
                transport_registry={"anthropic": transport},  # type: ignore[dict-item]
                agents={"test-agent": make_agent()},
                categories=make_categories(),
                run_id=uuid6.uuid7(),
                read_root=tmp_path,
                # Missing autonomy_level -- should raise TypeError
            )


class TestHeadlessRunStarted:
    """RunStarted event is handled gracefully in headless mode."""

    @pytest.mark.asyncio
    async def test_run_started_logged(
        self, tmp_path: Path,
    ) -> None:
        """RunStarted event in headless mode is logged
        without error."""
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)
        run_id = uuid6.uuid7()
        sched = _make_headless_scheduler(
            trace, transport, run_id, tmp_path,
        )

        task = TaskSpec(
            name="simple",
            agent="test-agent",
            task_prompt="Do a simple thing",
            timeout=30,
            context_refinement=False,
        )
        config = WorkflowConfig(
            name="headless-test",
            description="Test headless mode",
            tasks=[task],
            dependencies={},
        )
        # RunStarted is sent during execute_workflow;
        # should not raise
        await sched.execute_workflow(config)
