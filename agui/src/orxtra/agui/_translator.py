"""AGUITranslator -- stateful translator from orxtra events to AG-UI events."""

from __future__ import annotations

import time
import uuid
from typing import TYPE_CHECKING

from ag_ui.core import (
    BaseEvent,
    CustomEvent,
    RunErrorEvent,
    RunFinishedEvent,
    RunFinishedInterruptOutcome,
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
from ag_ui.core.types import Interrupt

if TYPE_CHECKING:
    from orxtra.protocols._types._events import OverseerEvent
    from orxtra.transport._events import TransportEvent


def _now_ms() -> int:
    return int(time.time() * 1000)


class AGUITranslator:
    """Stateful translator converting orxtra events to AG-UI protocol events.

    Maintains open-message state so that text streaming deltas are properly
    framed with START/CONTENT/END events.

    ``thinking_visibility`` controls how Thinking events are translated:
    - ``"silent"``   -- skip entirely
    - ``"overseer"`` -- emit only when ``is_overseer=True`` is passed
    - ``"agents"``   -- always emit
    """

    def __init__(
        self,
        thread_id: str,
        run_id: str,
        thinking_visibility: str = "silent",
    ) -> None:
        if thinking_visibility not in ("silent", "overseer", "agents"):
            msg = (
                f"thinking_visibility must be 'silent', 'overseer', or 'agents', "
                f"got {thinking_visibility!r}"
            )
            raise ValueError(msg)
        self._thread_id = thread_id
        self._run_id = run_id
        self._thinking_visibility = thinking_visibility
        self._text_message_open: bool = False
        self._current_message_id: str | None = None
        self._thinking_open: bool = False

    # -- internal helpers --

    def _close_text_message(self) -> list[BaseEvent]:
        """Close any open text message, returning the END event if needed."""
        if self._text_message_open and self._current_message_id is not None:
            end = TextMessageEndEvent(
                message_id=self._current_message_id,
                timestamp=_now_ms(),
            )
            self._text_message_open = False
            self._current_message_id = None
            return [end]
        return []

    def _close_thinking(self) -> list[BaseEvent]:
        """Close any open thinking message."""
        if self._thinking_open:
            self._thinking_open = False
            return [ThinkingTextMessageEndEvent(timestamp=_now_ms())]
        return []

    def _new_message_id(self) -> str:
        return str(uuid.uuid4())

    # -- transport event translation --

    def translate_transport(self, event: TransportEvent) -> list[BaseEvent]:
        """Translate a TransportEvent into zero or more AG-UI BaseEvents."""
        # Import here to avoid top-level coupling.
        from orxtra.transport import (
            Error,
            Result,
            StepFinish,
            StepStart,
            StreamDelta,
            Thinking,
            ToolExecuting,
            ToolUse,
        )

        if isinstance(event, StreamDelta):
            return self._handle_stream_delta(event)
        if isinstance(event, StepStart):
            return self._handle_step_start(event)
        if isinstance(event, StepFinish):
            return self._handle_step_finish(event)
        if isinstance(event, ToolExecuting):
            return self._handle_tool_executing(event)
        if isinstance(event, ToolUse):
            return self._handle_tool_use(event)
        if isinstance(event, Result):
            return self._handle_result(event)
        if isinstance(event, Error):
            return self._handle_error(event)
        if isinstance(event, Thinking):
            return self._handle_thinking(event)
        # Other transport events (ApiRetry, LivenessWarning, etc.) are skipped.
        return []

    def _handle_stream_delta(self, event: object) -> list[BaseEvent]:
        text = event.text  # type: ignore[attr-defined]
        ts = _now_ms()
        results: list[BaseEvent] = []

        # Close any open thinking first.
        results.extend(self._close_thinking())

        if not self._text_message_open:
            msg_id = self._new_message_id()
            self._current_message_id = msg_id
            self._text_message_open = True
            results.append(
                TextMessageStartEvent(
                    message_id=msg_id,
                    role="assistant",
                    timestamp=ts,
                )
            )
        results.append(
            TextMessageContentEvent(
                message_id=self._current_message_id,  # type: ignore[arg-type]
                delta=text,
                timestamp=ts,
            )
        )
        return results

    def _handle_step_start(self, event: object) -> list[BaseEvent]:
        session_id = event.session_id  # type: ignore[attr-defined]
        results: list[BaseEvent] = []
        results.extend(self._close_thinking())
        results.extend(self._close_text_message())
        results.append(
            StepStartedEvent(step_name=session_id, timestamp=_now_ms())
        )
        return results

    def _handle_step_finish(self, event: object) -> list[BaseEvent]:
        reason = event.reason  # type: ignore[attr-defined]
        results: list[BaseEvent] = []
        results.extend(self._close_thinking())
        results.extend(self._close_text_message())
        results.append(
            StepFinishedEvent(step_name=reason, timestamp=_now_ms())
        )
        return results

    def _handle_tool_executing(self, event: object) -> list[BaseEvent]:
        tool_use_id: str = event.tool_use_id  # type: ignore[attr-defined]
        tool_name: str = event.tool_name  # type: ignore[attr-defined]
        results: list[BaseEvent] = []
        results.extend(self._close_thinking())
        results.extend(self._close_text_message())
        results.append(
            ToolCallStartEvent(
                tool_call_id=tool_use_id,
                tool_call_name=tool_name,
                parent_message_id=self._current_message_id,
                timestamp=_now_ms(),
            )
        )
        return results

    def _handle_tool_use(self, event: object) -> list[BaseEvent]:
        tool_use_id: str = event.tool_use_id  # type: ignore[attr-defined]
        output: str = event.output  # type: ignore[attr-defined]
        ts = _now_ms()
        result_msg_id = self._new_message_id()
        return [
            ToolCallEndEvent(tool_call_id=tool_use_id, timestamp=ts),
            ToolCallResultEvent(
                message_id=result_msg_id,
                tool_call_id=tool_use_id,
                content=output,
                role="tool",
                timestamp=ts,
            ),
        ]

    def _handle_result(self, _event: object) -> list[BaseEvent]:
        results: list[BaseEvent] = []
        results.extend(self._close_thinking())
        results.extend(self._close_text_message())
        results.append(
            RunFinishedEvent(
                thread_id=self._thread_id,
                run_id=self._run_id,
                timestamp=_now_ms(),
            )
        )
        return results

    def _handle_error(self, event: object) -> list[BaseEvent]:
        name: str = event.name  # type: ignore[attr-defined]
        message: str = event.message  # type: ignore[attr-defined]
        return [
            RunErrorEvent(
                message=f"{name}: {message}",
                timestamp=_now_ms(),
            )
        ]

    def _handle_thinking(
        self,
        event: object,
        *,
        is_overseer: bool = False,
    ) -> list[BaseEvent]:
        if self._thinking_visibility == "silent":
            return []
        if self._thinking_visibility == "overseer" and not is_overseer:
            return []
        # "agents" or "overseer" with is_overseer=True
        text = event.text  # type: ignore[attr-defined]
        ts = _now_ms()
        results: list[BaseEvent] = []
        results.extend(self._close_text_message())
        if not self._thinking_open:
            self._thinking_open = True
            results.append(ThinkingTextMessageStartEvent(timestamp=ts))
        results.append(
            ThinkingTextMessageContentEvent(delta=text, timestamp=ts)
        )
        return results

    # -- overseer event translation --

    def translate_overseer(self, event: OverseerEvent) -> list[BaseEvent]:
        """Translate an OverseerEvent into zero or more AG-UI BaseEvents."""
        from orxtra.protocols import (
            BudgetExhausted,
            BudgetThresholdCrossed,
            HealthDegraded,
            InboxAnswered,
            InboxRejected,
            RunStarted,
            StructuralAdvisory,
            TaskEscalated,
            TaskFailed,
        )

        if isinstance(event, RunStarted):
            return self._handle_run_started(event)
        if isinstance(event, BudgetThresholdCrossed):
            return [self._custom("budget_threshold_crossed", {
                "workflow_id": str(event.workflow_id),
                "budget_usd": str(event.budget_usd),
                "spent_usd": str(event.spent_usd),
                "threshold_pct": event.threshold_pct,
            })]
        if isinstance(event, BudgetExhausted):
            return [self._custom("budget_exhausted", {
                "workflow_id": str(event.workflow_id),
            })]
        if isinstance(event, TaskFailed):
            return [self._custom("task_failed", {
                "task_id": str(event.task_id),
                "task_name": event.task_name,
            })]
        if isinstance(event, TaskEscalated):
            return [self._custom("task_escalated", {
                "task_id": str(event.task_id),
                "task_name": event.task_name,
                "from_child_task_id": str(event.from_child_task_id),
            })]
        if isinstance(event, InboxAnswered):
            return [self._custom("inbox_answered", {
                "item_id": str(event.item_id),
                "assumed_option": event.assumed_option,
                "actual_answer": event.actual_answer,
                "contradicts": event.contradicts,
            })]
        if isinstance(event, InboxRejected):
            return [self._custom("inbox_rejected", {
                "item_id": str(event.item_id),
                "rejection_reason": event.rejection_reason,
            })]
        if isinstance(event, StructuralAdvisory):
            return [self._custom("structural_advisory", {
                "task_id": str(event.task_id),
                "observation": event.observation,
                "suggestion": event.suggestion,
            })]
        if isinstance(event, HealthDegraded):
            return [self._custom("health_degraded", {
                "event_type": event.event_type,
                "failure_rate": event.failure_rate,
                "threshold": event.threshold,
            })]
        return []

    def _handle_run_started(self, _event: object) -> list[BaseEvent]:
        return [
            RunStartedEvent(
                thread_id=self._thread_id,
                run_id=self._run_id,
                timestamp=_now_ms(),
            )
        ]

    def _custom(self, name: str, value: object) -> BaseEvent:
        return CustomEvent(name=name, value=value, timestamp=_now_ms())

    # -- interrupt construction --

    def build_interrupt(
        self,
        *,
        item_id: str,
        question: str,
        options: list[str] | None = None,
        assumed_option: str | None = None,
        contradiction_impact: str | None = None,
        tags: list[str] | None = None,
        deadline: str | None = None,
    ) -> RunFinishedEvent:
        """Build a RunFinishedEvent with interrupt outcome for an inbox item."""
        response_schema: dict[str, object] | None = None
        if options:
            response_schema = {
                "type": "string",
                "enum": options,
            }

        metadata: dict[str, object] = {}
        if assumed_option is not None:
            metadata["assumed_option"] = assumed_option
        if contradiction_impact is not None:
            metadata["contradiction_impact"] = contradiction_impact
        if tags is not None:
            metadata["tags"] = tags
        if deadline is not None:
            metadata["deadline"] = deadline

        interrupt = Interrupt(
            id=item_id,
            reason="input_required",
            message=question,
            response_schema=response_schema,
            expires_at=deadline,
            metadata=metadata or None,
        )

        return RunFinishedEvent(
            thread_id=self._thread_id,
            run_id=self._run_id,
            outcome=RunFinishedInterruptOutcome(interrupts=[interrupt]),
            timestamp=_now_ms(),
        )

    # -- thinking visibility for overseer events --

    def translate_thinking_from_overseer(self, event: object) -> list[BaseEvent]:
        """Translate a Thinking event known to come from the overseer session."""
        return self._handle_thinking(event, is_overseer=True)
