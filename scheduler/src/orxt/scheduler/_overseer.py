from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any, Protocol

from orxt.protocols._events import (
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

if TYPE_CHECKING:
    from orxt.overseer._health import HealthMonitor
    from orxt.overseer._overseer import Overseer
    from orxt.session import Session

_logger = logging.getLogger("orxt.scheduler")

type OverseerEvent = (
    RunStarted
    | TaskFailed
    | TaskEscalated
    | BudgetThresholdCrossed
    | BudgetExhausted
    | InboxAnswered
    | InboxRejected
    | StructuralAdvisory
    | HealthDegraded
)

# Fallback behaviors when Overseer is degraded for an
# event type. Keys are event type names; values are short
# descriptions of what the scheduler does instead of
# consulting the Overseer.
FALLBACK_BEHAVIORS: dict[str, str] = {
    "TaskFailed": "fixed_escalation_ladder",
    "TaskEscalated": "fixed_escalation_ladder",
    "BudgetThresholdCrossed": (
        "maintain_current_allocations"
    ),
}
_DEFAULT_FALLBACK = "escalate_to_human_inbox"


class OverseerInterface(Protocol):
    """Interface the scheduler uses to communicate
    with the Overseer."""

    async def send_event(
        self, event: OverseerEvent,
    ) -> None: ...
    async def verify_actions(
        self, event_type: str = "",
    ) -> list[str]: ...
    async def send_correction(
        self, message: str,
    ) -> None: ...
    def is_degraded(
        self, event_type: str,
    ) -> bool: ...
    async def refine_context(
        self, task_name: str, raw_context: str,
    ) -> str: ...


class OverseerAdapter:
    """Bridges the scheduler's OverseerInterface protocol
    to the real Overseer instance."""

    def __init__(
        self,
        overseer: Overseer,
        health_monitor: HealthMonitor,
    ) -> None:
        self._overseer = overseer
        self._health_monitor = health_monitor
        self._last_tool_calls: dict[
            str, list[dict[str, Any]]
        ] = {}
        self._previous_tool_calls: dict[
            str, list[dict[str, Any]]
        ] = {}
        self._current_tool_calls: list[
            dict[str, Any]
        ] = []

    @property
    def session(self) -> Session:
        """Expose the overseer's session for handoff."""
        return self._overseer.session

    def update_session(
        self, new_session: Session,
    ) -> None:
        """Update the overseer's session after handoff."""
        self._overseer.session = new_session

    async def send_event(
        self, event: OverseerEvent,
    ) -> None:
        """Delegate to the Overseer and capture tool calls."""
        # Capture tool calls by iterating the session
        # stream directly instead of handle_event, so we
        # can inspect tool use events.
        from orxt.protocols import format_event  # noqa: PLC0415
        from orxt.transport import ToolUse  # noqa: PLC0415

        message = format_event(event)
        tool_calls: list[dict[str, Any]] = []
        async for ev in self._overseer.session.send(
            message,
        ):
            if isinstance(ev, ToolUse):
                tool_calls.append({  # noqa: PERF401
                    "tool_name": ev.tool_name,
                    "input": ev.input,
                    "status": ev.status,
                })

        event_type = type(event).__name__
        self._current_tool_calls = tool_calls
        self._last_tool_calls[event_type] = tool_calls

    async def send_correction(
        self, message: str,
    ) -> None:
        """Send a correction message to the Overseer
        session."""
        from orxt.transport import ToolUse  # noqa: PLC0415

        tool_calls: list[dict[str, Any]] = []
        async for ev in self._overseer.session.send(
            message,
        ):
            if isinstance(ev, ToolUse):
                tool_calls.append({  # noqa: PERF401
                    "tool_name": ev.tool_name,
                    "input": ev.input,
                    "status": ev.status,
                })
        self._current_tool_calls = tool_calls

    def is_degraded(
        self, event_type: str,
    ) -> bool:
        """Check if the Overseer is degraded for a
        given event type."""
        return self._health_monitor.is_degraded(
            event_type,
        )

    async def verify_actions(
        self, event_type: str = "",
    ) -> list[str]:
        """Run verification checks on the Overseer's
        response and return a list of error messages."""
        errors: list[str] = []

        # 1. Schema validation (stub)

        # 2. Constraint consistency: check for duplicate
        #    mechanical constraints on the same glob
        errors.extend(
            self._check_constraint_consistency(),
        )

        # 3. Repetition check
        errors.extend(
            self._check_repetition(event_type),
        )

        # 4. Proportionality check (stub)

        # Record health metrics
        is_repetition = any(
            "Repetition" in e for e in errors
        )
        self._health_monitor.record_event(
            event_type,
            success=len(errors) == 0,
            is_repetition=is_repetition,
        )

        return errors

    def _check_constraint_consistency(
        self,
    ) -> list[str]:
        """Check that no two add_constraint calls in the
        current response create mechanical constraints of
        the same kind on the same glob pattern."""
        errors: list[str] = []
        constraint_calls = [
            tc for tc in self._current_tool_calls
            if tc["tool_name"] == "add_constraint"
        ]
        seen: dict[tuple[str, str], int] = {}
        for tc in constraint_calls:
            inp = tc["input"]
            kind = inp.get("kind", "")
            glob = inp.get("glob", "")
            key = (kind, glob)
            if key in seen:
                errors.append(
                    f"Constraint consistency: duplicate"
                    f" mechanical constraint"
                    f" kind={kind!r} glob={glob!r}"
                )
            else:
                seen[key] = 1
        return errors

    def _check_repetition(
        self, event_type: str,
    ) -> list[str]:
        """Compare current tool calls to previous calls
        for the same event type. Flag if identical."""
        errors: list[str] = []
        if not event_type:
            return errors

        current = self._current_tool_calls
        previous = self._previous_tool_calls.get(
            event_type,
        )
        if (
            previous is not None
            and current
            and previous
        ):
            current_sig = json.dumps(
                [
                    {
                        "tool_name": tc["tool_name"],
                        "input": tc["input"],
                    }
                    for tc in current
                ],
                sort_keys=True,
            )
            previous_sig = json.dumps(
                [
                    {
                        "tool_name": tc["tool_name"],
                        "input": tc["input"],
                    }
                    for tc in previous
                ],
                sort_keys=True,
            )
            if current_sig == previous_sig:
                errors.append(
                    f"Repetition detected:"
                    f" identical tool calls for"
                    f" event type {event_type!r}"
                )

        # Store current as previous for next comparison
        self._previous_tool_calls[event_type] = current

        return errors

    async def refine_context(
        self, task_name: str, raw_context: str,
    ) -> str:
        """Send context to Overseer for refinement.

        This is a request/response, not an event.
        The Overseer can read files, consult, etc.
        Returns the refined context text.
        """
        from orxt.transport import Result  # noqa: PLC0415

        message = (
            f"Refine this agent context for task"
            f" '{task_name}'. You may add relevant"
            f" lessons, request additional code context,"
            f" reorder, or accept as-is.\n\n"
            f"--- RAW CONTEXT ---\n{raw_context}\n"
            f"--- END RAW CONTEXT ---"
        )
        parts: list[str] = [
            ev.text
            async for ev in self._overseer.session.send(
                message,
            )
            if isinstance(ev, Result)
        ]
        return "".join(parts) or raw_context
