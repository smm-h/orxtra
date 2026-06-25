from __future__ import annotations

import asyncio
import importlib
import logging

from orxtra.protocols import (
    Action,
    ActionExecutor,
    EventAction,
    EventFireCallback,
    LogAction,
    ScriptAction,
    WorkflowAction,
)

logger = logging.getLogger(__name__)


async def execute_action(
    action: Action,
    events: list[dict[str, object]],
    *,
    workflow_executor: ActionExecutor | None = None,
    event_fire_callback: EventFireCallback | None = None,
) -> None:
    """Dispatch a single action by type.

    Follows verify's ``execute_check`` pattern: isinstance dispatch on
    the Action union, hard error on unknown types.
    """
    if isinstance(action, ScriptAction):
        await _run_script(action, events)
        return
    if isinstance(action, LogAction):
        _run_log(action, events)
        return
    if isinstance(action, WorkflowAction):
        await _run_workflow(action, events, workflow_executor)
        return
    if isinstance(action, EventAction):
        await _run_event(action, event_fire_callback)
        return
    msg = f"Unknown action type: {type(action).__name__}"
    raise TypeError(msg)


async def execute_actions_bounded(
    actions: list[tuple[Action, list[dict[str, object]]]],
    *,
    max_concurrent: int,
    workflow_executor: ActionExecutor | None = None,
    event_fire_callback: EventFireCallback | None = None,
) -> None:
    """Execute multiple actions with bounded concurrency."""
    semaphore = asyncio.Semaphore(max_concurrent)

    async def _guarded(action: Action, evts: list[dict[str, object]]) -> None:
        async with semaphore:
            await execute_action(
                action,
                evts,
                workflow_executor=workflow_executor,
                event_fire_callback=event_fire_callback,
            )

    tasks = [
        asyncio.create_task(_guarded(action, evts))
        for action, evts in actions
    ]
    if tasks:
        await asyncio.gather(*tasks)


# -- Per-type runners --


async def _run_script(action: ScriptAction, events: list[dict[str, object]]) -> None:
    parts = action.callable.split(":")
    if len(parts) != 2:  # noqa: PLR2004
        msg = (
            f"Invalid callable path: {action.callable!r}"
            " (expected 'module:function')"
        )
        raise ValueError(msg)
    module_path, func_name = parts
    try:
        module = importlib.import_module(module_path)
    except ImportError as e:
        msg = f"Module not found: {module_path!r}"
        raise ImportError(msg) from e
    func = getattr(module, func_name, None)
    if func is None:
        msg = f"Function {func_name!r} not found in module {module_path!r}"
        raise AttributeError(msg)
    result = func(events)
    if asyncio.iscoroutine(result):
        await result


def _run_log(action: LogAction, events: list[dict[str, object]]) -> None:
    level = getattr(logging, action.level.upper(), logging.INFO)
    event_count = len(events)
    logger.log(
        level,
        "%s (events=%d)",
        action.message,
        event_count,
    )


async def _run_workflow(
    action: WorkflowAction,
    events: list[dict[str, object]],
    executor: ActionExecutor | None,
) -> None:
    if executor is None:
        msg = (
            "WorkflowAction requires an ActionExecutor but none was provided"
        )
        raise RuntimeError(msg)
    await executor.execute_workflow(action.workflow_path, action.config, events)


async def _run_event(
    action: EventAction,
    callback: EventFireCallback | None,
) -> None:
    if callback is None:
        msg = (
            "EventAction requires an event_fire_callback but none was provided"
        )
        raise RuntimeError(msg)
    await callback(action.event_type, action.data if action.data else None)
