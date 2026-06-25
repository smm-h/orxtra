"""Tests for agent context window awareness with automatic handoff."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.scheduler._executor import Scheduler

from .conftest import MockTraceWriter, MockTransport, make_agent, make_categories

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable


class TestComputeContextUsage:
    """Tests for _compute_context_usage calculation."""

    def test_zero_tokens(
        self,
        scheduler: Scheduler,
    ) -> None:
        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 0
        session.total_output_tokens = 0
        tokens, pct = scheduler._compute_context_usage(session)
        assert tokens == 0
        assert pct == 0.0

    def test_half_usage(
        self,
        scheduler: Scheduler,
    ) -> None:
        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 50_000
        session.total_output_tokens = 50_000
        tokens, pct = scheduler._compute_context_usage(session)
        assert tokens == 100_000
        assert pct == pytest.approx(0.5)

    def test_over_ninety_percent(
        self,
        make_scheduler: Callable[..., Scheduler],
    ) -> None:
        sched = make_scheduler(model_context_limit=100_000)
        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 80_000
        session.total_output_tokens = 11_000
        tokens, pct = sched._compute_context_usage(session)
        assert tokens == 91_000
        assert pct == pytest.approx(0.91)

    def test_zero_context_limit(
        self,
        make_scheduler: Callable[..., Scheduler],
    ) -> None:
        sched = make_scheduler(model_context_limit=0)
        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 1000
        session.total_output_tokens = 500
        tokens, pct = sched._compute_context_usage(session)
        assert tokens == 1500
        assert pct == 0.0


class TestCheckAgentContext:
    """Tests for _check_agent_context behavior at different thresholds."""

    async def test_below_eighty_no_event(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Below 80% usage: no context warning event."""
        sched = make_scheduler(model_context_limit=100_000)

        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 30_000
        session.total_output_tokens = 10_000
        session._session_id = "test-session-1"

        task_id = uuid6.uuid7()
        sched._task_specs[task_id] = None  # type: ignore[assignment]

        await sched._check_agent_context(session, "test-session-1", task_id)

        context_events = [
            c for c in trace_writer.calls
            if c[0] == "write_event" and c[1].get("event_type") == "context_warning"
        ]
        assert len(context_events) == 0

    async def test_eighty_percent_emits_warning(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """At 80% usage: emits context warning event with action=warning."""
        sched = make_scheduler(model_context_limit=100_000)

        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 50_000
        session.total_output_tokens = 30_000  # 80% total
        session._session_id = "test-session-2"

        task_id = uuid6.uuid7()
        sched._task_specs[task_id] = None  # type: ignore[assignment]

        await sched._check_agent_context(session, "test-session-2", task_id)

        context_events = trace_writer.get_calls("write_event")
        warning_events = [
            e for e in context_events
            if e.get("event_type") == "context_warning"
        ]
        assert len(warning_events) == 1
        data = warning_events[0]["data"]
        assert data["action"] == "warning"
        assert data["usage_percent"] == 80.0
        assert data["tokens_used"] == 80_000
        assert data["context_limit"] == 100_000

    async def test_eighty_five_percent_emits_warning(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """At 85% usage: still warning, not handoff."""
        sched = make_scheduler(model_context_limit=100_000)

        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 50_000
        session.total_output_tokens = 35_000  # 85% total
        session._session_id = "test-session-3"

        task_id = uuid6.uuid7()
        sched._task_specs[task_id] = None  # type: ignore[assignment]

        await sched._check_agent_context(session, "test-session-3", task_id)

        context_events = trace_writer.get_calls("write_event")
        warning_events = [
            e for e in context_events
            if e.get("event_type") == "context_warning"
        ]
        assert len(warning_events) == 1
        assert warning_events[0]["data"]["action"] == "warning"

    async def test_ninety_percent_triggers_handoff(
        self,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """At 90% usage: emits context warning with action=handoff."""
        # Use a non-auto-execute transport for handoff test
        # so the summary send doesn't trigger start_task
        transport = MockTransport(auto_execute_tools=False)
        agents = {"test-agent": make_agent()}
        sched = Scheduler(
            trace_writer=trace_writer,  # type: ignore[arg-type]
            transport_registry={"anthropic": transport},  # type: ignore[dict-item]
            agents=agents,
            categories=make_categories(),
            run_id=run_id,
            read_root=tmp_path,
            autonomy_level="max",
            model_context_limit=100_000,
        )

        from orxtra.protocols import TaskSpec
        from orxtra.session import Session

        task_spec = TaskSpec(
            name="test-task",
            agent="test-agent",
            task_prompt="Do the thing",
        )

        task_id = uuid6.uuid7()
        sched._task_specs[task_id] = task_spec

        # Use a valid UUID for the session_id so transcript writes work
        sid = str(uuid6.uuid7())

        session = Session.__new__(Session)
        session.total_input_tokens = 50_000
        session.total_output_tokens = 41_000  # 91% total
        session._session_id = sid
        session._transport = transport
        session._model = "anthropic/claude-sonnet-4-6"
        session._system_prompt = "test"
        session._tools = []
        session._trace_writer = trace_writer
        session._run_id = sched._run_id
        session.total_reasoning_tokens = 0
        session.total_cache_read_tokens = 0
        session.total_cache_write_tokens = 0
        session.turn_count = 5
        from collections import defaultdict
        session._event_handlers = defaultdict(list)

        sched._task_sessions[task_id] = session

        await sched._check_agent_context(session, sid, task_id)

        context_events = trace_writer.get_calls("write_event")
        handoff_events = [
            e for e in context_events
            if e.get("event_type") == "context_warning"
        ]
        assert len(handoff_events) == 1
        assert handoff_events[0]["data"]["action"] == "handoff"

    async def test_exact_eighty_percent_boundary(
        self,
        make_scheduler: Callable[..., Scheduler],
        trace_writer: MockTraceWriter,
    ) -> None:
        """Exactly 80% triggers warning."""
        sched = make_scheduler(model_context_limit=200_000)

        from orxtra.session import Session

        session = Session.__new__(Session)
        session.total_input_tokens = 100_000
        session.total_output_tokens = 60_000  # exactly 80%
        session._session_id = "test-session-5"

        task_id = uuid6.uuid7()
        sched._task_specs[task_id] = None  # type: ignore[assignment]

        await sched._check_agent_context(session, "test-session-5", task_id)

        context_events = trace_writer.get_calls("write_event")
        warning_events = [
            e for e in context_events
            if e.get("event_type") == "context_warning"
        ]
        assert len(warning_events) == 1
        assert warning_events[0]["data"]["action"] == "warning"


class TestContextWarningEvent:
    """Tests for the ContextWarning event dataclass."""

    def test_context_warning_fields(self) -> None:
        from orxtra.transport import ContextWarning

        warning = ContextWarning(
            session_id="sess-1",
            usage_percent=85.5,
            tokens_used=171_000,
            context_limit=200_000,
        )
        assert warning.session_id == "sess-1"
        assert warning.usage_percent == 85.5
        assert warning.tokens_used == 171_000
        assert warning.context_limit == 200_000

    def test_context_warning_is_frozen(self) -> None:
        from orxtra.transport import ContextWarning

        warning = ContextWarning(
            session_id="sess-1",
            usage_percent=85.5,
            tokens_used=171_000,
            context_limit=200_000,
        )
        with pytest.raises(AttributeError):
            warning.session_id = "other"  # type: ignore[misc]

    def test_context_warning_in_event_union(self) -> None:
        """ContextWarning is part of the Event union type."""
        from orxtra.transport import ContextWarning, Event

        warning = ContextWarning(
            session_id="sess-1",
            usage_percent=85.5,
            tokens_used=171_000,
            context_limit=200_000,
        )
        # This should type-check as Event
        event: Event = warning
        assert isinstance(event, ContextWarning)
