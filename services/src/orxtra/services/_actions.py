from __future__ import annotations

from typing import TYPE_CHECKING, Any

from orxtra.dispatch import execute_action
from orxtra.protocols import Action, ActionExecutor, EventAction, EventFireCallback, WorkflowAction

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    import asyncpg


class ServicesActionExecutor:
    """Concrete ActionExecutor that bridges dispatch to services.

    Injects service-level concerns into action execution:
    - WorkflowAction: delegates to ``start_run_from_file``
    - EventAction: delegates to ``fire_event``

    Satisfies the ``ActionExecutor`` protocol from ``orxtra.dispatch``.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,
        *,
        intent_prefix: str = "dispatch",
    ) -> None:
        self._pool = pool
        self._intent_prefix = intent_prefix

    async def execute_workflow(
        self,
        workflow_path: str,
        config: dict[str, object],
        events: list[dict[str, object]],
    ) -> None:
        # Lazy import to avoid circular dependency at module level.
        from pathlib import Path

        from orxtra.services._run import start_run_from_file

        path = Path(workflow_path)
        intent = f"{self._intent_prefix}: {path.stem} ({len(events)} events)"
        await start_run_from_file(self._pool, intent, path)


async def execute_service_action(
    action: Action,
    events: list[dict[str, object]],
    *,
    pool: asyncpg.Pool | None = None,
    intent_prefix: str = "dispatch",
) -> None:
    """Execute an action using service-level executors.

    Convenience function that wires up a ServicesActionExecutor
    and an event fire callback, then delegates to dispatch's
    ``execute_action``.
    """
    executor: ServicesActionExecutor | None = None
    if pool is not None:
        executor = ServicesActionExecutor(pool, intent_prefix=intent_prefix)

    event_callback: EventFireCallback | None = None
    if pool is not None:

        async def _fire_event(
            event_type: str,
            data: dict[str, object] | None,
        ) -> None:
            from orxtra.services._events import fire_event

            await fire_event(pool, None, event_type, data)

        event_callback = _fire_event

    await execute_action(
        action,
        events,
        workflow_executor=executor,
        event_fire_callback=event_callback,
    )
