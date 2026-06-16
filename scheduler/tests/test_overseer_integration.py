"""Tests for Overseer integration in the scheduler.

Covers event sending, verify-then-accept loop,
degraded mode fallback, coherence summary, session
handoff, repetition detection, and constraint
consistency.
"""

from __future__ import annotations

import logging
import sys
import types
from decimal import Decimal
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import uuid6
from orxt.protocols._events import (
    BudgetThresholdCrossed,
    HealthDegraded,
    InboxAnswered,
    InboxRejected,
    RunStarted,
    StructuralAdvisory,
    TaskFailed,
)
from orxt.protocols._task import (
    EscalationPayload,
    TaskContext,
    TaskSpec,
)
from orxt.scheduler._executor import Scheduler
from orxt.scheduler._overseer import (
    _DEFAULT_FALLBACK,
    FALLBACK_BEHAVIORS,
    OverseerAdapter,
)
from orxt.scheduler._types import WorkflowConfig
from orxt.transport import Result

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)

if TYPE_CHECKING:
    import pytest


# -- Mock infrastructure ----------------------------------


class MockOverseerSession:
    """Mock session for Overseer handoff/coherence."""

    def __init__(
        self,
        response_text: str = (
            "Mock coherence summary"
        ),
        total_input_tokens: int = 0,
        total_output_tokens: int = 0,
    ) -> None:
        self._response_text = response_text
        self.total_input_tokens = (
            total_input_tokens
        )
        self.total_output_tokens = (
            total_output_tokens
        )
        self.messages_sent: list[str] = []
        self._model = "test-model"
        self._system_prompt = "test"
        self._tools: list[object] = []
        self._transport = None
        self._run_id = None

    @property
    def model(self) -> str:
        return self._model

    @property
    def system_prompt(self) -> str:
        return self._system_prompt

    @property
    def tools(self) -> list[object]:
        return self._tools

    def resume_id(self) -> str:
        return "mock-session-id"

    async def send(  # noqa: ANN201
        self, message: str,
    ):
        self.messages_sent.append(message)
        yield Result(
            text=self._response_text,
            session_id="mock-session",
            total_input_tokens=10,
            total_output_tokens=5,
        )


class MockOverseerAdapter:
    """Mock implementing OverseerInterface."""

    def __init__(self) -> None:
        self.events: list[object] = []
        self.corrections: list[str] = []
        self.verify_results: list[list[str]] = []
        self.degraded_types: set[str] = set()
        self.verify_call_count = 0
        self.mock_session: (
            MockOverseerSession | None
        ) = None

    async def send_event(
        self, event: object,
    ) -> None:
        self.events.append(event)

    async def verify_actions(
        self, event_type: str = "",
    ) -> list[str]:
        self.verify_call_count += 1
        if self.verify_results:
            return self.verify_results.pop(0)
        return []

    async def send_correction(
        self, message: str,
    ) -> None:
        self.corrections.append(message)

    def is_degraded(
        self, event_type: str,
    ) -> bool:
        return event_type in self.degraded_types

    @property
    def session(self) -> MockOverseerSession:
        if self.mock_session is None:
            self.mock_session = (
                MockOverseerSession()
            )
        return self.mock_session

    def update_session(
        self, new_session: object,
    ) -> None:
        self.mock_session = new_session  # type: ignore[assignment]


class MockHealthMonitor:
    """Minimal HealthMonitor stand-in."""

    def __init__(self) -> None:
        self.events: list[
            tuple[str, bool, bool]
        ] = []

    def record_event(
        self,
        event_type: str,
        success: bool,
        is_repetition: bool = False,
    ) -> None:
        self.events.append(
            (event_type, success, is_repetition),
        )

    def is_degraded(
        self, event_type: str,
    ) -> bool:
        return False


# -- Helpers ----------------------------------------------

# _check_session_handoff does a local import of
# orxt.overseer which is not installed in the
# scheduler test environment. Patch it to a no-op
# for tests that exercise _send_overseer_event.
_PATCH_HANDOFF = patch.object(
    Scheduler,
    "_check_session_handoff",
    new_callable=AsyncMock,
)


def _make_scheduler(
    trace_writer: MockTraceWriter | None = None,
    overseer: MockOverseerAdapter | None = None,
    model_context_limit: int = 200_000,
) -> Scheduler:
    tw = trace_writer or MockTraceWriter()
    transport = MockTransport()
    agents = {"test-agent": make_agent()}
    categories = make_categories()
    run_id = uuid6.uuid7()
    return Scheduler(
        trace_writer=tw,  # type: ignore[arg-type]
        transport_registry={
            "anthropic": transport,  # type: ignore[dict-item]
        },
        agents=agents,
        categories=categories,
        run_id=run_id,
        overseer_interface=overseer,  # type: ignore[arg-type]
        model_context_limit=model_context_limit,
    )


def _run_started_event() -> RunStarted:
    return RunStarted(
        intent="test intent",
        config_snapshot={"key": "value"},
    )


def _task_failed_event() -> TaskFailed:
    tid = uuid6.uuid7()
    rid = uuid6.uuid7()
    return TaskFailed(
        task_id=tid,
        task_name="failed-task",
        payload=EscalationPayload(
            task_name="failed-task",
            task_id=tid,
            agent_name="test-agent",
            attempts=1,
            failed_checks=[],
            agent_summary="it failed",
            context=TaskContext(
                variables={},
                run_id=rid,
                task_name="failed-task",
                task_id=tid,
                attempt=1,
                prior_attempts=None,
                notepad_content="",
                parent_task_id=None,
                nesting_depth=0,
            ),
        ),
    )


def _inbox_answered_event() -> InboxAnswered:
    return InboxAnswered(
        item_id=uuid6.uuid7(),
        assumed_option="A",
        actual_answer="B",
        contradicts=True,
    )


def _inbox_rejected_event() -> InboxRejected:
    return InboxRejected(
        item_id=uuid6.uuid7(),
        rejection_reason="invalid",
    )


def _structural_advisory_event() -> (
    StructuralAdvisory
):
    return StructuralAdvisory(
        task_id=uuid6.uuid7(),
        observation="observed",
        suggestion="suggested",
    )


def _budget_threshold_event() -> (
    BudgetThresholdCrossed
):
    return BudgetThresholdCrossed(
        workflow_id=uuid6.uuid7(),
        budget_usd=Decimal("10.00"),
        spent_usd=Decimal("8.00"),
        threshold_pct=0.8,
    )


def _simple_task(
    name: str = "t1",
    agent: str = "test-agent",
) -> TaskSpec:
    return TaskSpec(
        name=name,
        agent=agent,
        task_prompt=f"Do {name}",
        timeout=60,
        context_refinement=False,
    )


def _simple_workflow() -> WorkflowConfig:
    return WorkflowConfig(
        name="test-workflow",
        description="Test workflow",
        tasks=[_simple_task()],
        dependencies={},
    )


def _install_fake_overseer_module() -> (
    types.ModuleType
):
    """Inject a fake orxt.overseer._handoff module
    into sys.modules so that the local import in
    _check_session_handoff succeeds."""
    fake_overseer = types.ModuleType(
        "orxt.overseer",
    )
    fake_handoff = types.ModuleType(
        "orxt.overseer._handoff",
    )
    fake_handoff.check_handoff_needed = AsyncMock(  # type: ignore[attr-defined]
        return_value=False,
    )
    fake_handoff.perform_handoff = AsyncMock(  # type: ignore[attr-defined]
        return_value=None,
    )
    fake_overseer._handoff = fake_handoff  # type: ignore[attr-defined]  # noqa: SLF001
    sys.modules.setdefault(
        "orxt.overseer", fake_overseer,
    )
    sys.modules.setdefault(
        "orxt.overseer._handoff", fake_handoff,
    )
    return fake_handoff


def _make_adapter_for_verify() -> (
    tuple[OverseerAdapter, MockHealthMonitor]
):
    """Create an OverseerAdapter with bypassed __init__
    and a mock health monitor, for testing verify_actions
    and its sub-checks."""
    monitor = MockHealthMonitor()
    adapter = OverseerAdapter.__new__(
        OverseerAdapter,
    )
    adapter._health_monitor = monitor  # type: ignore[assignment]  # noqa: SLF001
    adapter._last_tool_calls = {}  # noqa: SLF001
    adapter._previous_tool_calls = {}  # noqa: SLF001
    adapter._current_tool_calls = []  # noqa: SLF001
    return adapter, monitor


# -- Tests ------------------------------------------------


class TestOverseerEventSending:
    """Events are forwarded to the adapter."""

    async def test_run_started_event_sent(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        scheduler = _make_scheduler(
            overseer=adapter,
        )
        event = _run_started_event()
        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )
        assert len(adapter.events) == 1
        assert adapter.events[0] is event

    async def test_task_failed_event_sent(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        scheduler = _make_scheduler(
            overseer=adapter,
        )
        event = _task_failed_event()
        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )
        assert len(adapter.events) == 1
        assert adapter.events[0] is event

    async def test_inbox_answered_event_sent(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        scheduler = _make_scheduler(
            overseer=adapter,
        )
        event = _inbox_answered_event()
        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )
        assert len(adapter.events) == 1
        assert adapter.events[0] is event

    async def test_inbox_rejected_event_sent(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        scheduler = _make_scheduler(
            overseer=adapter,
        )
        event = _inbox_rejected_event()
        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )
        assert len(adapter.events) == 1
        assert adapter.events[0] is event

    async def test_structural_advisory_event_sent(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        scheduler = _make_scheduler(
            overseer=adapter,
        )
        event = _structural_advisory_event()
        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )
        assert len(adapter.events) == 1
        assert adapter.events[0] is event

    async def test_budget_threshold_crossed_sent(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        scheduler = _make_scheduler(
            overseer=adapter,
        )
        event = _budget_threshold_event()
        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )
        assert len(adapter.events) == 1
        assert adapter.events[0] is event

    async def test_no_overseer_skips_silently(
        self,
    ) -> None:
        scheduler = _make_scheduler(overseer=None)
        event = _run_started_event()
        # No overseer -> returns immediately,
        # _check_session_handoff never reached.
        await scheduler._send_overseer_event(event)  # noqa: SLF001


class TestVerifyThenAcceptLoop:
    """The 3-attempt verify-then-accept retry loop."""

    async def test_no_errors_single_attempt(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.verify_results = [[]]
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                _run_started_event(),
            )

        assert len(adapter.events) == 1
        assert len(adapter.corrections) == 0
        assert adapter.verify_call_count == 1

    async def test_error_then_pass_two_attempts(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.verify_results = [["error"], []]
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                _run_started_event(),
            )

        assert len(adapter.events) == 1
        assert len(adapter.corrections) == 1
        assert adapter.verify_call_count == 2

    async def test_three_failures_logs_warning(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.verify_results = [
            ["err1"],
            ["err2"],
            ["err3"],
        ]
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        with (
            _PATCH_HANDOFF,
            caplog.at_level(
                logging.WARNING,
                logger="orxt.scheduler",
            ),
        ):
            await scheduler._send_overseer_event(  # noqa: SLF001
                _run_started_event(),
            )

        assert len(adapter.events) == 1
        assert len(adapter.corrections) == 2
        assert adapter.verify_call_count == 3
        assert any(
            "failed verification" in r.message
            for r in caplog.records
        )

    async def test_correction_contains_errors(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.verify_results = [
            ["bad thing 1", "bad thing 2"],
            [],
        ]
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                _run_started_event(),
            )

        assert len(adapter.corrections) == 1
        msg = adapter.corrections[0]
        assert "bad thing 1" in msg
        assert "bad thing 2" in msg


class TestDegradedMode:
    """Degraded mode uses fallback behaviors."""

    async def test_degraded_event_skips_overseer(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.degraded_types.add("TaskFailed")
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        # Degraded path returns before handoff check,
        # so no patch needed.
        await scheduler._send_overseer_event(  # noqa: SLF001
            _task_failed_event(),
        )

        assert len(adapter.events) == 0

    async def test_degraded_uses_fallback_behavior(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.degraded_types.add("TaskFailed")
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        with caplog.at_level(
            logging.WARNING,
            logger="orxt.scheduler",
        ):
            await scheduler._send_overseer_event(  # noqa: SLF001
                _task_failed_event(),
            )

        expected = FALLBACK_BEHAVIORS["TaskFailed"]
        assert any(
            expected in r.message
            for r in caplog.records
        )

    async def test_non_degraded_proceeds_normally(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.degraded_types.add("TaskFailed")
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        with _PATCH_HANDOFF:
            await scheduler._send_overseer_event(  # noqa: SLF001
                _run_started_event(),
            )

        assert len(adapter.events) == 1

    async def test_default_fallback_for_unknown(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.degraded_types.add(
            "HealthDegraded",
        )
        scheduler = _make_scheduler(
            overseer=adapter,
        )

        event = HealthDegraded(
            event_type="TaskFailed",
            failure_rate=0.5,
            threshold=0.3,
        )
        with caplog.at_level(
            logging.WARNING,
            logger="orxt.scheduler",
        ):
            await scheduler._send_overseer_event(  # noqa: SLF001
                event,
            )

        assert any(
            _DEFAULT_FALLBACK in r.message
            for r in caplog.records
        )


class TestCoherenceSummary:
    """Coherence summary at run end."""

    async def test_summary_written_at_run_end(
        self,
    ) -> None:
        tw = MockTraceWriter()
        adapter = MockOverseerAdapter()
        adapter.mock_session = MockOverseerSession(
            response_text="test summary",
        )
        scheduler = _make_scheduler(
            trace_writer=tw, overseer=adapter,
        )

        await scheduler._write_coherence_summary()  # noqa: SLF001

        calls = tw.get_calls(
            "write_coherence_summary",
        )
        assert len(calls) == 1
        assert calls[0]["summary"] == "test summary"

    async def test_summary_skipped_without_overseer(
        self,
    ) -> None:
        tw = MockTraceWriter()
        scheduler = _make_scheduler(
            trace_writer=tw, overseer=None,
        )

        await scheduler._write_coherence_summary()  # noqa: SLF001

        calls = tw.get_calls(
            "write_coherence_summary",
        )
        assert len(calls) == 0


class TestSessionHandoff:
    """Session handoff based on token usage.

    The scheduler's _check_session_handoff does a
    local import of orxt.overseer._handoff which is
    not installed. We inject a fake module into
    sys.modules before patching its attributes.
    """

    async def test_handoff_triggered_at_threshold(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.mock_session = MockOverseerSession(
            total_input_tokens=180_001,
            total_output_tokens=0,
        )
        scheduler = _make_scheduler(
            overseer=adapter,
            model_context_limit=200_000,
        )

        new_session = MockOverseerSession(
            response_text="new session",
        )
        fake_mod = _install_fake_overseer_module()
        fake_mod.check_handoff_needed = AsyncMock(  # type: ignore[attr-defined]
            return_value=True,
        )
        fake_mod.perform_handoff = AsyncMock(  # type: ignore[attr-defined]
            return_value=new_session,
        )

        try:
            await scheduler._check_session_handoff()  # noqa: SLF001
        finally:
            sys.modules.pop(
                "orxt.overseer._handoff", None,
            )
            sys.modules.pop(
                "orxt.overseer", None,
            )

        assert adapter.mock_session is new_session

    async def test_handoff_not_triggered_below(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.mock_session = MockOverseerSession(
            total_input_tokens=1000,
            total_output_tokens=0,
        )
        scheduler = _make_scheduler(
            overseer=adapter,
            model_context_limit=200_000,
        )
        original = adapter.mock_session

        fake_mod = _install_fake_overseer_module()
        mock_perform = AsyncMock(
            return_value=None,
        )
        fake_mod.check_handoff_needed = AsyncMock(  # type: ignore[attr-defined]
            return_value=False,
        )
        fake_mod.perform_handoff = mock_perform  # type: ignore[attr-defined]

        try:
            await scheduler._check_session_handoff()  # noqa: SLF001
        finally:
            sys.modules.pop(
                "orxt.overseer._handoff", None,
            )
            sys.modules.pop(
                "orxt.overseer", None,
            )

        mock_perform.assert_not_called()
        assert adapter.mock_session is original

    async def test_handoff_new_session_used(
        self,
    ) -> None:
        adapter = MockOverseerAdapter()
        adapter.mock_session = MockOverseerSession(
            total_input_tokens=190_000,
            total_output_tokens=0,
        )
        scheduler = _make_scheduler(
            overseer=adapter,
            model_context_limit=200_000,
        )

        new_session = MockOverseerSession(
            response_text="fresh session",
            total_input_tokens=50,
            total_output_tokens=10,
        )
        fake_mod = _install_fake_overseer_module()
        fake_mod.check_handoff_needed = AsyncMock(  # type: ignore[attr-defined]
            return_value=True,
        )
        fake_mod.perform_handoff = AsyncMock(  # type: ignore[attr-defined]
            return_value=new_session,
        )

        try:
            await scheduler._check_session_handoff()  # noqa: SLF001
        finally:
            sys.modules.pop(
                "orxt.overseer._handoff", None,
            )
            sys.modules.pop(
                "orxt.overseer", None,
            )

        assert adapter.session is new_session
        assert (
            adapter.session.total_input_tokens == 50
        )


class TestRepetitionDetection:
    """OverseerAdapter detects identical tool calls."""

    async def test_repetition_flagged(
        self,
    ) -> None:
        adapter, _ = _make_adapter_for_verify()
        adapter._previous_tool_calls = {  # noqa: SLF001
            "TaskFailed": [
                {
                    "tool_name": "add_constraint",
                    "input": {"kind": "budget"},
                },
            ],
        }
        adapter._current_tool_calls = [  # noqa: SLF001
            {
                "tool_name": "add_constraint",
                "input": {"kind": "budget"},
            },
        ]

        errors = await adapter.verify_actions(
            "TaskFailed",
        )

        assert any(
            "Repetition" in e for e in errors
        )

    async def test_no_repetition_for_different(
        self,
    ) -> None:
        adapter, _ = _make_adapter_for_verify()
        adapter._previous_tool_calls = {  # noqa: SLF001
            "TaskFailed": [
                {
                    "tool_name": "add_constraint",
                    "input": {"kind": "budget"},
                },
            ],
        }
        adapter._current_tool_calls = [  # noqa: SLF001
            {
                "tool_name": "add_constraint",
                "input": {"kind": "timeout"},
            },
        ]

        errors = await adapter.verify_actions(
            "TaskFailed",
        )

        assert not any(
            "Repetition" in e for e in errors
        )


class TestConstraintConsistency:
    """OverseerAdapter detects duplicate constraints."""

    async def test_duplicate_constraints_flagged(
        self,
    ) -> None:
        adapter, _ = _make_adapter_for_verify()
        adapter._current_tool_calls = [  # noqa: SLF001
            {
                "tool_name": "add_constraint",
                "input": {
                    "kind": "budget",
                    "glob": "*.py",
                },
            },
            {
                "tool_name": "add_constraint",
                "input": {
                    "kind": "budget",
                    "glob": "*.py",
                },
            },
        ]

        errors = await adapter.verify_actions()

        assert any(
            "Constraint consistency" in e
            for e in errors
        )

    async def test_different_constraints_pass(
        self,
    ) -> None:
        adapter, _ = _make_adapter_for_verify()
        adapter._current_tool_calls = [  # noqa: SLF001
            {
                "tool_name": "add_constraint",
                "input": {
                    "kind": "budget",
                    "glob": "*.py",
                },
            },
            {
                "tool_name": "add_constraint",
                "input": {
                    "kind": "timeout",
                    "glob": "*.py",
                },
            },
        ]

        errors = await adapter.verify_actions()

        assert not any(
            "Constraint consistency" in e
            for e in errors
        )
