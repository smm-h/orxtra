"""Tests for AG-UI event translation, sinks, interrupts, state, and thinking."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

import pytest
import uuid6
from ag_ui.core import (
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunStartedEvent,
    StepFinishedEvent,
    StepStartedEvent,
    TextMessageContentEvent,
    TextMessageEndEvent,
    TextMessageStartEvent,
    ThinkingTextMessageContentEvent,
    ThinkingTextMessageEndEvent,
    ThinkingTextMessageStartEvent,
    ToolCallEndEvent,
    ToolCallResultEvent,
    ToolCallStartEvent,
)
from orxtra.agui._sinks import AGUIOverseerSink, AGUITransportSink
from orxtra.agui._state import StateManager
from orxtra.agui._translator import AGUITranslator
from orxtra.protocols import (
    BudgetExhausted,
    BudgetThresholdCrossed,
    EscalationPayload,
    HealthDegraded,
    InboxAnswered,
    InboxRejected,
    RunStarted,
    StructuralAdvisory,
    TaskContext,
    TaskEscalated,
    TaskFailed,
)
from orxtra.transport._events import (
    Error,
    Result,
    StepFinish,
    StepStart,
    StreamDelta,
    Thinking,
    ToolExecuting,
    ToolUse,
)


@pytest.fixture
def translator() -> AGUITranslator:
    return AGUITranslator(
        thread_id="thread-1",
        run_id="run-1",
        thinking_visibility="silent",
    )


# ── StreamDelta framing ──


class TestStreamDeltaFraming:
    def test_first_delta_emits_start_and_content(
        self, translator: AGUITranslator,
    ) -> None:
        events = translator.translate_transport(StreamDelta(text="hello"))
        assert len(events) == 2
        assert isinstance(events[0], TextMessageStartEvent)
        assert events[0].role == "assistant"
        assert isinstance(events[1], TextMessageContentEvent)
        assert events[1].delta == "hello"
        # Both share the same message_id.
        assert events[0].message_id == events[1].message_id

    def test_subsequent_delta_emits_content_only(
        self, translator: AGUITranslator,
    ) -> None:
        translator.translate_transport(StreamDelta(text="a"))
        events = translator.translate_transport(StreamDelta(text="b"))
        assert len(events) == 1
        assert isinstance(events[0], TextMessageContentEvent)
        assert events[0].delta == "b"

    def test_message_id_consistent_across_deltas(
        self, translator: AGUITranslator,
    ) -> None:
        first = translator.translate_transport(StreamDelta(text="a"))
        second = translator.translate_transport(StreamDelta(text="b"))
        assert first[1].message_id == second[0].message_id


# ── Text message auto-close ──


class TestTextMessageAutoClose:
    def test_step_start_closes_open_message(
        self, translator: AGUITranslator,
    ) -> None:
        translator.translate_transport(StreamDelta(text="hello"))
        events = translator.translate_transport(StepStart(session_id="s1"))
        # Should contain TextMessageEndEvent then StepStartedEvent.
        types = [type(e) for e in events]
        assert TextMessageEndEvent in types
        assert StepStartedEvent in types
        # End comes before Start.
        end_idx = types.index(TextMessageEndEvent)
        start_idx = types.index(StepStartedEvent)
        assert end_idx < start_idx

    def test_tool_executing_closes_open_message(
        self, translator: AGUITranslator,
    ) -> None:
        translator.translate_transport(StreamDelta(text="hi"))
        events = translator.translate_transport(
            ToolExecuting(
                tool_use_id="tu-1",
                tool_name="read_file",
                tool_input={"path": "/tmp/test"},
            )
        )
        types = [type(e) for e in events]
        assert TextMessageEndEvent in types
        assert ToolCallStartEvent in types

    def test_result_closes_open_message(
        self, translator: AGUITranslator,
    ) -> None:
        translator.translate_transport(StreamDelta(text="hi"))
        events = translator.translate_transport(
            Result(text="done", session_id="s1")
        )
        types = [type(e) for e in events]
        assert TextMessageEndEvent in types
        assert RunFinishedEvent in types

    def test_no_double_close(
        self, translator: AGUITranslator,
    ) -> None:
        """If no message is open, closing events should not emit TextMessageEnd."""
        events = translator.translate_transport(StepStart(session_id="s1"))
        types = [type(e) for e in events]
        assert TextMessageEndEvent not in types


# ── ToolUse translation ──


class TestToolUseTranslation:
    def test_tool_executing_emits_start(
        self, translator: AGUITranslator,
    ) -> None:
        events = translator.translate_transport(
            ToolExecuting(
                tool_use_id="tu-1",
                tool_name="write_file",
                tool_input={"path": "/tmp/out"},
            )
        )
        starts = [e for e in events if isinstance(e, ToolCallStartEvent)]
        assert len(starts) == 1
        assert starts[0].tool_call_id == "tu-1"
        assert starts[0].tool_call_name == "write_file"

    def test_tool_use_emits_end_and_result(
        self, translator: AGUITranslator,
    ) -> None:
        events = translator.translate_transport(
            ToolUse(
                tool_use_id="tu-1",
                tool_name="write_file",
                input={"path": "/tmp/out"},
                output="OK",
                status="success",
            )
        )
        assert len(events) == 2
        assert isinstance(events[0], ToolCallEndEvent)
        assert events[0].tool_call_id == "tu-1"
        assert isinstance(events[1], ToolCallResultEvent)
        assert events[1].tool_call_id == "tu-1"
        assert events[1].content == "OK"
        assert events[1].role == "tool"

    def test_tool_use_id_correlation(
        self, translator: AGUITranslator,
    ) -> None:
        """ToolCallStart and ToolCallEnd/Result share the same tool_call_id."""
        translator.translate_transport(
            ToolExecuting(
                tool_use_id="tu-42",
                tool_name="exec",
                tool_input={},
            )
        )
        end_events = translator.translate_transport(
            ToolUse(
                tool_use_id="tu-42",
                tool_name="exec",
                input={},
                output="done",
                status="success",
            )
        )
        assert all(
            e.tool_call_id == "tu-42"
            for e in end_events
            if hasattr(e, "tool_call_id")
        )


# ── Overseer events ──


class TestOverseerTranslation:
    def test_run_started(self, translator: AGUITranslator) -> None:
        events = translator.translate_overseer(
            RunStarted(intent="deploy", config_snapshot={})
        )
        assert len(events) == 1
        assert isinstance(events[0], RunStartedEvent)
        assert events[0].thread_id == "thread-1"
        assert events[0].run_id == "run-1"

    def test_budget_threshold_crossed(
        self, translator: AGUITranslator,
    ) -> None:
        wf_id = uuid6.uuid7()
        events = translator.translate_overseer(
            BudgetThresholdCrossed(
                workflow_id=wf_id,
                budget_usd=Decimal("10.00"),
                spent_usd=Decimal("8.00"),
                threshold_pct=0.8,
            )
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "budget_threshold_crossed"

    def test_budget_exhausted(self, translator: AGUITranslator) -> None:
        events = translator.translate_overseer(
            BudgetExhausted(workflow_id=uuid6.uuid7())
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "budget_exhausted"

    def test_task_failed(self, translator: AGUITranslator) -> None:
        task_id = uuid6.uuid7()
        context = TaskContext(
            variables={},
            run_id=uuid6.uuid7(),
            task_name="failing",
            task_id=task_id,
            attempt=1,
            prior_attempts=None,
            notepad_content="",
            parent_task_id=None,
            nesting_depth=0,
        )
        payload = EscalationPayload(
            task_name="failing",
            task_id=task_id,
            agent_name="coder",
            attempts=1,
            failed_checks=[],
            agent_summary="failed",
            context=context,
        )
        events = translator.translate_overseer(
            TaskFailed(task_id=task_id, task_name="failing", payload=payload)
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "task_failed"

    def test_task_escalated(self, translator: AGUITranslator) -> None:
        task_id = uuid6.uuid7()
        child_id = uuid6.uuid7()
        context = TaskContext(
            variables={},
            run_id=uuid6.uuid7(),
            task_name="escalating",
            task_id=task_id,
            attempt=1,
            prior_attempts=None,
            notepad_content="",
            parent_task_id=None,
            nesting_depth=0,
        )
        payload = EscalationPayload(
            task_name="escalating",
            task_id=task_id,
            agent_name="coder",
            attempts=1,
            failed_checks=[],
            agent_summary="escalated",
            context=context,
        )
        events = translator.translate_overseer(
            TaskEscalated(
                task_id=task_id,
                task_name="escalating",
                from_child_task_id=child_id,
                payload=payload,
            )
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "task_escalated"

    def test_inbox_answered(self, translator: AGUITranslator) -> None:
        events = translator.translate_overseer(
            InboxAnswered(
                item_id=uuid6.uuid7(),
                assumed_option="yes",
                actual_answer="no",
                contradicts=True,
            )
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "inbox_answered"

    def test_inbox_rejected(self, translator: AGUITranslator) -> None:
        events = translator.translate_overseer(
            InboxRejected(
                item_id=uuid6.uuid7(),
                rejection_reason="invalid",
            )
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "inbox_rejected"

    def test_structural_advisory(self, translator: AGUITranslator) -> None:
        events = translator.translate_overseer(
            StructuralAdvisory(
                task_id=uuid6.uuid7(),
                observation="obs",
                suggestion="sug",
            )
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "structural_advisory"

    def test_health_degraded(self, translator: AGUITranslator) -> None:
        events = translator.translate_overseer(
            HealthDegraded(
                event_type="api_error",
                failure_rate=0.5,
                threshold=0.3,
            )
        )
        assert len(events) == 1
        assert isinstance(events[0], CustomEvent)
        assert events[0].name == "health_degraded"


# ── Error translation ──


class TestErrorTranslation:
    def test_error_emits_run_error(self, translator: AGUITranslator) -> None:
        events = translator.translate_transport(
            Error(name="APIError", message="rate limited")
        )
        assert len(events) == 1
        assert isinstance(events[0], RunErrorEvent)
        assert "APIError" in events[0].message
        assert "rate limited" in events[0].message


# ── Step events ──


class TestStepEvents:
    def test_step_start(self, translator: AGUITranslator) -> None:
        events = translator.translate_transport(StepStart(session_id="sess-1"))
        starts = [e for e in events if isinstance(e, StepStartedEvent)]
        assert len(starts) == 1
        assert starts[0].step_name == "sess-1"

    def test_step_finish(self, translator: AGUITranslator) -> None:
        events = translator.translate_transport(
            StepFinish(reason="end_turn")
        )
        finishes = [e for e in events if isinstance(e, StepFinishedEvent)]
        assert len(finishes) == 1
        assert finishes[0].step_name == "end_turn"


# ── Interrupt construction ──


class TestInterruptConstruction:
    def test_basic_interrupt(self, translator: AGUITranslator) -> None:
        event = translator.build_interrupt(
            item_id="inbox-1",
            question="Continue deployment?",
            options=["yes", "no", "abort"],
            assumed_option="yes",
        )
        assert isinstance(event, RunFinishedEvent)
        assert event.thread_id == "thread-1"
        assert event.run_id == "run-1"
        assert event.outcome is not None
        assert event.outcome.type == "interrupt"
        assert len(event.outcome.interrupts) == 1

        interrupt = event.outcome.interrupts[0]
        assert interrupt.id == "inbox-1"
        assert interrupt.reason == "input_required"
        assert interrupt.message == "Continue deployment?"
        assert interrupt.response_schema == {
            "type": "string",
            "enum": ["yes", "no", "abort"],
        }
        assert interrupt.metadata is not None
        assert interrupt.metadata["assumed_option"] == "yes"

    def test_interrupt_with_metadata(self, translator: AGUITranslator) -> None:
        event = translator.build_interrupt(
            item_id="inbox-2",
            question="Approve?",
            tags=["deploy", "prod"],
            deadline="2026-07-01T00:00:00Z",
            contradiction_impact="high",
        )
        interrupt = event.outcome.interrupts[0]
        assert interrupt.metadata is not None
        assert interrupt.metadata["tags"] == ["deploy", "prod"]
        assert interrupt.metadata["deadline"] == "2026-07-01T00:00:00Z"
        assert interrupt.metadata["contradiction_impact"] == "high"
        assert interrupt.expires_at == "2026-07-01T00:00:00Z"

    def test_interrupt_without_options(
        self, translator: AGUITranslator,
    ) -> None:
        event = translator.build_interrupt(
            item_id="inbox-3",
            question="What is the target branch?",
        )
        interrupt = event.outcome.interrupts[0]
        assert interrupt.response_schema is None
        assert interrupt.metadata is None


# ── State snapshots and deltas ──


class TestStateManager:
    async def test_snapshot_basic(self) -> None:
        mgr = StateManager()
        event = await mgr.snapshot(run_id="run-1")
        assert event.snapshot == {"run_id": "run-1"}
        assert mgr.last_state == {"run_id": "run-1"}

    async def test_snapshot_with_queries(self) -> None:
        async def query_run(run_id: str) -> dict[str, Any]:
            return {"status": "running", "id": run_id}

        async def query_tasks(run_id: str) -> list[dict[str, Any]]:
            return [{"name": "build", "status": "pending"}]

        mgr = StateManager()
        event = await mgr.snapshot(
            run_id="run-1",
            query_run=query_run,
            query_tasks=query_tasks,
        )
        assert event.snapshot["run"] == {"status": "running", "id": "run-1"}
        assert event.snapshot["tasks"] == [{"name": "build", "status": "pending"}]

    def test_compute_delta_produces_patch(self) -> None:
        mgr = StateManager()
        old = {"run_id": "run-1", "status": "running"}
        new = {"run_id": "run-1", "status": "finished"}
        event = mgr.compute_delta(old, new)
        assert event is not None
        # JSON Patch should have a replace operation.
        assert len(event.delta) >= 1
        ops = event.delta
        replace_ops = [op for op in ops if op.get("op") == "replace"]
        assert len(replace_ops) == 1
        assert replace_ops[0]["path"] == "/status"
        assert replace_ops[0]["value"] == "finished"

    def test_compute_delta_returns_none_for_identical(self) -> None:
        mgr = StateManager()
        state = {"run_id": "run-1", "status": "running"}
        event = mgr.compute_delta(state, state)
        assert event is None

    def test_compute_delta_add_key(self) -> None:
        mgr = StateManager()
        old = {"run_id": "run-1"}
        new = {"run_id": "run-1", "inbox": []}
        event = mgr.compute_delta(old, new)
        assert event is not None
        add_ops = [op for op in event.delta if op.get("op") == "add"]
        assert len(add_ops) == 1
        assert add_ops[0]["path"] == "/inbox"


# ── Thinking visibility ──


class TestThinkingVisibility:
    def test_silent_skips_thinking(self) -> None:
        t = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="silent",
        )
        events = t.translate_transport(Thinking(text="hmm"))
        assert events == []

    def test_agents_always_emits(self) -> None:
        t = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="agents",
        )
        events = t.translate_transport(Thinking(text="reasoning"))
        assert len(events) >= 1
        types = [type(e) for e in events]
        assert ThinkingTextMessageStartEvent in types
        assert ThinkingTextMessageContentEvent in types

    def test_overseer_skips_non_overseer(self) -> None:
        t = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="overseer",
        )
        # Regular translate_transport is not from overseer.
        events = t.translate_transport(Thinking(text="hidden"))
        assert events == []

    def test_overseer_emits_for_overseer(self) -> None:
        t = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="overseer",
        )
        events = t.translate_thinking_from_overseer(Thinking(text="visible"))
        assert len(events) >= 1
        types = [type(e) for e in events]
        assert ThinkingTextMessageStartEvent in types
        assert ThinkingTextMessageContentEvent in types

    def test_thinking_closes_open_text_message(self) -> None:
        t = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="agents",
        )
        t.translate_transport(StreamDelta(text="hi"))
        events = t.translate_transport(Thinking(text="think"))
        types = [type(e) for e in events]
        assert TextMessageEndEvent in types

    def test_stream_delta_closes_open_thinking(self) -> None:
        t = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="agents",
        )
        t.translate_transport(Thinking(text="think"))
        events = t.translate_transport(StreamDelta(text="hi"))
        types = [type(e) for e in events]
        assert ThinkingTextMessageEndEvent in types

    def test_invalid_visibility_raises(self) -> None:
        with pytest.raises(ValueError, match="thinking_visibility"):
            AGUITranslator(
                thread_id="t", run_id="r", thinking_visibility="invalid",
            )


# ── Sinks ──


class TestSinks:
    async def test_transport_sink_forwards_events(self) -> None:
        received: list[Any] = []

        async def callback(event: Any) -> None:
            received.append(event)

        translator = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="silent",
        )
        sink = AGUITransportSink(translator, callback)
        await sink.on_event(StreamDelta(text="hello"))

        assert len(received) == 2
        assert isinstance(received[0], TextMessageStartEvent)
        assert isinstance(received[1], TextMessageContentEvent)

    async def test_overseer_sink_forwards_events(self) -> None:
        received: list[Any] = []

        async def callback(event: Any) -> None:
            received.append(event)

        translator = AGUITranslator(
            thread_id="t", run_id="r", thinking_visibility="silent",
        )
        sink = AGUIOverseerSink(translator, callback)
        await sink.on_event(
            RunStarted(intent="test", config_snapshot={})
        )

        assert len(received) == 1
        assert isinstance(received[0], RunStartedEvent)


# ── Timestamps ──


class TestTimestamps:
    def test_events_have_timestamps(self, translator: AGUITranslator) -> None:
        events = translator.translate_transport(StreamDelta(text="hi"))
        for event in events:
            assert event.timestamp is not None
            assert event.timestamp > 0
