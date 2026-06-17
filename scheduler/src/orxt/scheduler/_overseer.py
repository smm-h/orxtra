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
    from collections.abc import Awaitable, Callable

    from orxt.overseer._autonomy import AutonomyLevel
    from orxt.overseer._health import HealthMonitor
    from orxt.overseer._overseer import Overseer
    from orxt.protocols._tool import Tool
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


async def _fixed_escalation_ladder(
    event: OverseerEvent,
    logger: logging.Logger,
    *,
    trace_writer: Any = None,
    run_id: Any = None,
) -> None:
    """For TaskFailed/TaskEscalated: log and let the
    scheduler's own retry mechanism handle it."""
    _ = trace_writer, run_id
    event_type = type(event).__name__
    logger.info(
        "Degraded mode: fixed escalation for %s",
        event_type,
    )


async def _maintain_current_allocations(
    event: OverseerEvent,
    logger: logging.Logger,
    *,
    trace_writer: Any = None,
    run_id: Any = None,
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
    trace_writer: Any = None,
    run_id: Any = None,
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


FALLBACK_HANDLERS: dict[
    str,
    Callable[..., Awaitable[None]],
] = {
    "fixed_escalation_ladder": (
        _fixed_escalation_ladder
    ),
    "maintain_current_allocations": (
        _maintain_current_allocations
    ),
    "escalate_to_human_inbox": (
        _escalate_to_human_inbox
    ),
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

    def __init__(
        self,
        overseer: Overseer,
        health_monitor: HealthMonitor,
        autonomy_level: AutonomyLevel | None = None,
    ) -> None:
        if autonomy_level is None:
            from orxt.overseer._autonomy import AutonomyLevel as _AL  # noqa: PLC0415
            autonomy_level = _AL.MAX
        self._overseer = overseer
        self._health_monitor = health_monitor
        self._autonomy_level = autonomy_level
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
        from orxt.overseer._autonomy import is_autonomous  # noqa: PLC0415
        from orxt.protocols._tool import Tool as ToolCls  # noqa: PLC0415

        action_type = TOOL_ACTION_TYPES.get(
            tool.name, "scope_change",
        )
        if is_autonomous(
            self._autonomy_level, action_type,
        ):
            return tool

        level_value = self._autonomy_level.value

        async def _gated_execute(
            args: dict[str, Any],
        ) -> str:
            _ = args
            return (
                f"Action blocked: tool"
                f" '{tool.name}' requires"
                f" '{action_type}' autonomy"
                f" (current level:"
                f" '{level_value}')."
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
        for field in required:
            if field not in args:
                errors.append(
                    f"Schema: create_task missing"
                    f" required field '{field}'"
                )

        if errors:
            return errors

        # Try to construct a TaskSpec and validate
        try:
            from orxt.protocols._task import TaskSpec  # noqa: PLC0415
            from orxt.scheduler._types import WorkflowConfig  # noqa: PLC0415
            from orxt.scheduler._validator import validate_task_tree  # noqa: PLC0415

            task = TaskSpec(**args)
            config = WorkflowConfig(
                name="validation",
                description="Schema validation",
                tasks=[task],
                dependencies={},
            )
            tree_errors = validate_task_tree(config)
            for te in tree_errors:
                errors.append(f"Schema: {te}")
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
        for field in required:
            if field not in args:
                errors.append(
                    f"Schema: create_workflow missing"
                    f" required field '{field}'"
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
