from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Protocol

from orxtra.protocols import (
    BudgetExhausted,
    BudgetThresholdCrossed,
    HealthDegraded,
    InboxAnswered,
    InboxRejected,
    OverseerEvent,
    RunStarted,
    StructuralAdvisory,
    TaskEscalated,
    TaskFailed,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from orxtra.protocols import (
        AutonomyLevel,
        HealthMonitorProtocol,
        OverseerProtocol,
        SessionProtocol,
        Tool,
    )

_logger = logging.getLogger("orxtra.scheduler")

# Fallback behaviors when Overseer is degraded for an
# event type. Keys are event type names; values are short
# descriptions of what the scheduler does instead of
# consulting the Overseer.
FALLBACK_BEHAVIORS: dict[str, str] = {
    "TaskFailed": "write_to_trace",
    "TaskEscalated": "write_to_trace",
    "BudgetThresholdCrossed": (
        "maintain_current_allocations"
    ),
    "BudgetExhausted": "maintain_current_allocations",
    "RunStarted": "log_only",
    "StructuralAdvisory": "write_to_trace",
}
_DEFAULT_FALLBACK = "escalate_to_human_inbox"


async def _maintain_current_allocations(
    event: OverseerEvent,
    logger: logging.Logger,
    *,
    trace_writer: Any = None,  # noqa: ANN401
    run_id: UUID | None = None,
) -> None:
    """For budget events: do nothing, just log."""
    _ = trace_writer, run_id
    event_type = type(event).__name__
    logger.info(
        "Degraded mode: maintaining current"
        " allocations for %s",
        event_type,
    )


async def _escalate_to_human_inbox(
    event: OverseerEvent,
    logger: logging.Logger,
    *,
    trace_writer: Any = None,  # noqa: ANN401
    run_id: UUID | None = None,
) -> None:
    """Default fallback: create an inbox item for
    human intervention."""
    event_type = type(event).__name__
    logger.info(
        "Degraded mode: escalating %s to human"
        " inbox",
        event_type,
    )
    if trace_writer is not None and run_id is not None:
        await trace_writer.create_inbox_item(
            run_id=run_id,
            decision_type="degraded_escalation",
            question=(
                f"Overseer is degraded. Event"
                f" {event_type} needs human review."
            ),
            options=[
                {
                    "label": "acknowledge",
                    "description": (
                        "Acknowledge and continue"
                    ),
                },
            ],
        )


async def _write_to_trace(
    event: OverseerEvent,
    logger: logging.Logger,
    *,
    trace_writer: Any = None,  # noqa: ANN401
    run_id: UUID | None = None,
) -> None:
    """Write the event to trace as a headless fallback
    record. For TaskFailed/TaskEscalated, also creates
    an inbox item for human review."""
    event_type = type(event).__name__
    logger.warning(
        "Headless fallback: writing %s to trace",
        event_type,
    )
    if trace_writer is not None and run_id is not None:
        import dataclasses  # noqa: PLC0415

        data: dict[str, Any] = {
            "event_type": event_type,
            "headless_fallback": True,
        }
        if (
            dataclasses.is_dataclass(event)
            and not isinstance(event, type)
        ):
            for f in dataclasses.fields(event):
                val = getattr(event, f.name)
                data[f.name] = str(val)
        await trace_writer.write_event(
            run_id=run_id,
            event_type=f"headless_{event_type}",
            data=data,
        )
        # For failure/escalation events, create an
        # inbox item for human review
        if event_type in (
            "TaskFailed", "TaskEscalated",
        ):
            task_name = getattr(
                event, "task_name", "unknown",
            )
            await trace_writer.create_inbox_item(
                run_id=run_id,
                decision_type="headless_escalation",
                question=(
                    f"Task '{task_name}' escalated in"
                    f" headless mode. Human review"
                    f" required."
                ),
                options=[
                    {
                        "label": "acknowledge",
                        "description": (
                            "Acknowledge and continue"
                        ),
                    },
                ],
            )


async def _log_only(
    event: OverseerEvent,
    logger: logging.Logger,
    *,
    trace_writer: Any = None,  # noqa: ANN401
    run_id: UUID | None = None,
) -> None:
    """Log the event, no other action needed."""
    _ = trace_writer, run_id
    event_type = type(event).__name__
    logger.info(
        "Headless mode: %s (no action needed)",
        event_type,
    )


FALLBACK_HANDLERS: dict[
    str,
    Callable[..., Awaitable[None]],
] = {
    "maintain_current_allocations": (
        _maintain_current_allocations
    ),
    "escalate_to_human_inbox": (
        _escalate_to_human_inbox
    ),
    "write_to_trace": _write_to_trace,
    "log_only": _log_only,
}

# Maps Overseer tool names to autonomy action types.
# Action types from _autonomy.py: read_only, retry,
# budget_reallocation, concurrency, task_assumption,
# scope_change, architecture_decision,
# understanding_assumption
TOOL_ACTION_TYPES: dict[str, str] = {
    # Read tools are always allowed
    "read": "read_only",
    "list_dir": "read_only",
    "glob": "read_only",
    "grep": "read_only",
    "stat": "read_only",
    "diff": "read_only",
    "notepad": "read_only",
    # Memory tools
    "record_decision": "task_assumption",
    "record_assumption": "task_assumption",
    "write_lesson": "task_assumption",
    "update_workflow_status": "read_only",
    # Constraint tools
    "add_constraint": "architecture_decision",
    # Inbox/escalation
    "create_inbox_item": "read_only",
    # Lifecycle tools -- scope changes
    "create_workflow": "scope_change",
    "create_task": "scope_change",
    "start_task": "retry",
    "end_task": "retry",
    "create_wait_for": "scope_change",
    "await_task": "retry",
    # Consult is read-only
    "consult": "read_only",
}


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

    def __init__(  # noqa: PLR0913
        self,
        overseer: OverseerProtocol,
        health_monitor: HealthMonitorProtocol,
        autonomy_level: AutonomyLevel | None = None,
        budget_limit: Decimal | None = None,
        spent_fn: Callable[[], Decimal] | None = None,
        proportionality_threshold: float | None = None,
    ) -> None:
        if autonomy_level is None:
            from orxtra.protocols import AutonomyLevel  # noqa: PLC0415
            autonomy_level = AutonomyLevel.MAX
        self._overseer = overseer
        self._health_monitor = health_monitor
        self._autonomy_level = autonomy_level
        self._budget_limit = budget_limit
        self._spent_fn = spent_fn
        self._proportionality_threshold = proportionality_threshold
        self._last_tool_calls: dict[
            str, list[dict[str, Any]]
        ] = {}
        self._previous_tool_calls: dict[
            str, list[dict[str, Any]]
        ] = {}
        self._current_tool_calls: list[
            dict[str, Any]
        ] = []
        self._gate_session_tools()

    def _gate_session_tools(self) -> None:
        """Replace the Overseer session's tools with
        autonomy-gated versions."""
        session = self._overseer.session
        session.update_tools(
            self.gate_tools(session.tools),
        )

    @property
    def session(self) -> SessionProtocol:
        """Expose the overseer's session for handoff."""
        return self._overseer.session

    def update_session(
        self, new_session: SessionProtocol,
    ) -> None:
        """Update the overseer's session after handoff."""
        self._overseer.session = new_session
        self._gate_session_tools()

    def gate_tools(
        self, tools: list[Tool],
    ) -> list[Tool]:
        """Wrap tools with autonomy gating.

        Tools whose action type is not autonomous at
        the current level get a replacement execute
        function that returns a blocked message instead
        of running the original.
        """
        return [self._gate_tool(t) for t in tools]

    def _gate_tool(self, tool: Tool) -> Tool:
        """Wrap a single tool with autonomy gating."""
        from orxtra.protocols import Confirmation, ToolOutput  # noqa: PLC0415
        from orxtra.protocols import Tool as ToolCls  # noqa: PLC0415

        action_type = TOOL_ACTION_TYPES.get(
            tool.name, "scope_change",
        )
        if self._autonomy_level.is_autonomous(
            action_type,
        ):
            return tool

        level_value = self._autonomy_level.value

        async def _gated_execute(
            args: dict[str, Any],
        ) -> ToolOutput[Confirmation]:
            _ = args
            msg = (
                f"Action blocked: tool"
                f" '{tool.name}' requires"
                f" '{action_type}' autonomy"
                f" (current level:"
                f" '{level_value}')."
            )
            return ToolOutput(
                data=Confirmation(message=msg),
                text=msg,
            )

        return ToolCls(
            name=tool.name,
            description=tool.description,
            parameters=tool.parameters,
            execute=_gated_execute,
            suspending=tool.suspending,
        )

    async def send_event(
        self, event: OverseerEvent,
    ) -> None:
        """Delegate to the Overseer and capture tool calls."""
        # Capture tool calls by iterating the session
        # stream directly instead of using the Overseer's
        # session, so we can inspect tool use events.
        from orxtra.transport import ToolUse  # noqa: PLC0415

        message = self._overseer.prepare_event(event)
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
        from orxtra.transport import ToolUse  # noqa: PLC0415

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

        # 1. Schema validation
        errors.extend(
            self._check_schema_validation(),
        )

        # 2. Constraint consistency: check for duplicate
        #    mechanical constraints on the same glob
        errors.extend(
            self._check_constraint_consistency(),
        )

        # 3. Repetition check
        errors.extend(
            self._check_repetition(event_type),
        )

        # 4. Proportionality check
        errors.extend(
            self._check_proportionality(),
        )

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

    def _check_schema_validation(
        self,
    ) -> list[str]:
        """Validate create_task/create_workflow tool call
        args against TaskSpec/WorkflowConfig schema."""
        errors: list[str] = []

        for tc in self._current_tool_calls:
            tool_name = tc["tool_name"]
            args = tc["input"]

            if tool_name == "create_task":
                task_errors = (
                    self._validate_create_task_args(args)
                )
                errors.extend(task_errors)
            elif tool_name == "create_workflow":
                wf_errors = (
                    self._validate_create_workflow_args(
                        args,
                    )
                )
                errors.extend(wf_errors)

        return errors

    @staticmethod
    def _validate_create_task_args(
        args: dict[str, Any],
    ) -> list[str]:
        """Validate create_task tool call arguments."""
        errors: list[str] = []

        # Check required fields
        required = [
            "name",
            "agent",
            "task_prompt",
            "timeout",
            "context_refinement",
        ]
        errors.extend(
            f"Schema: create_task missing"
            f" required field '{field}'"
            for field in required
            if field not in args
        )

        if errors:
            return errors

        # Try to construct a TaskSpec and validate
        try:
            from orxtra.protocols import TaskSpec  # noqa: PLC0415
            from orxtra.scheduler._types import WorkflowConfig  # noqa: PLC0415
            from orxtra.scheduler._validator import validate_task_tree  # noqa: PLC0415

            task = TaskSpec(**args)
            config = WorkflowConfig(
                name="validation",
                description="Schema validation",
                tasks=[task],
                dependencies={},
            )
            tree_errors = validate_task_tree(config)
            errors.extend(
                f"Schema: {te}"
                for te in tree_errors
            )
        except Exception as e:  # noqa: BLE001
            errors.append(
                f"Schema: create_task args invalid:"
                f" {e}"
            )

        return errors

    @staticmethod
    def _validate_create_workflow_args(
        args: dict[str, Any],
    ) -> list[str]:
        """Validate create_workflow tool call arguments."""
        errors: list[str] = []

        required = ["name", "description", "goals"]
        errors.extend(
            f"Schema: create_workflow missing"
            f" required field '{field}'"
            for field in required
            if field not in args
        )

        if errors:
            return errors

        # Validate goals is a non-empty list
        goals = args.get("goals", [])
        if not goals:
            errors.append(
                "Schema: create_workflow requires"
                " at least one goal"
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

    def _check_proportionality(
        self,
    ) -> list[str]:
        """Flag create_workflow budgets that exceed
        the configured fraction of remaining run budget."""
        if self._proportionality_threshold is None:
            if self._budget_limit is not None:
                return [
                    "proportionality_threshold required"
                    " when budget is set",
                ]
            return []
        if self._budget_limit is None:
            return []
        spent = (
            self._spent_fn()
            if self._spent_fn is not None
            else Decimal(0)
        )
        remaining = self._budget_limit - spent
        if remaining <= 0:
            return []

        threshold = remaining * Decimal(
            str(self._proportionality_threshold),
        )
        errors: list[str] = []
        for tc in self._current_tool_calls:
            if tc["tool_name"] != "create_workflow":
                continue
            budget_str = tc.get("input", {}).get("budget")
            if budget_str is None:
                continue
            try:
                budget = Decimal(str(budget_str))
            except Exception:  # noqa: BLE001, S112
                continue
            if budget > threshold:
                errors.append(
                    f"Proportionality: create_workflow"
                    f" budget ${budget} exceeds"
                    f" {self._proportionality_threshold:.0%}"
                    f" of remaining run budget"
                    f" ${remaining}"
                )
        return errors

    async def refine_context(
        self, task_name: str, raw_context: str,
    ) -> str:
        """Send context to Overseer for refinement.

        This is a request/response, not an event.
        The Overseer can read files, consult, etc.
        Returns the refined context text.
        """
        from orxtra.transport import Result  # noqa: PLC0415

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
