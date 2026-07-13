from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.dispatch import execute_action
from orxtra.protocols import (
    Action,
    EventFireCallback,
)

if TYPE_CHECKING:

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
        config: dict[str, object],  # noqa: ARG002 -- ActionExecutor protocol signature
        events: list[dict[str, object]],
    ) -> None:
        # Lazy import to avoid circular dependency at module level.
        from pathlib import Path

        from orxtra.identity import PgPrincipalStorage
        from orxtra.protocols import KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF
        from orxtra.services._run import start_run_from_file

        # A dispatch-triggered workflow has no human caller: the creating actor
        # is the singleton system principal. Resolve it explicitly (hard error
        # if unseeded -- matches resolve_caller_principal's contract).
        storage = PgPrincipalStorage(self._pool)
        system_principal = await storage.get_principal_by_ref(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF,
        )
        if system_principal is None:
            msg = (
                "System principal not seeded -- run 'orxtra db init' to seed "
                "the singleton system principal before dispatching workflows."
            )
            raise RuntimeError(msg)

        path = Path(workflow_path)
        intent = f"{self._intent_prefix}: {path.stem} ({len(events)} events)"
        await start_run_from_file(
            self._pool, storage, system_principal, intent, path,
        )


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
        from orxtra.identity import PgPrincipalStorage
        from orxtra.protocols import KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF

        # A dispatch-fired event has no human caller: attribute it to the
        # singleton system principal. Resolve it ONCE here (hard error if
        # unseeded -- matches resolve_caller_principal's contract).
        storage = PgPrincipalStorage(pool)
        system_principal = await storage.get_principal_by_ref(
            KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF,
        )
        if system_principal is None:
            msg = (
                "System principal not seeded -- run 'orxtra db init' to seed "
                "the singleton system principal before firing dispatch events."
            )
            raise RuntimeError(msg)

        resolved_system = system_principal

        async def _fire_event(
            event_type: str,
            data: dict[str, object] | None,
        ) -> None:
            from orxtra.services._events import fire_event

            await fire_event(
                pool, resolved_system,
                run_id=None, event_name=event_type, payload=data,
            )

        event_callback = _fire_event

    await execute_action(
        action,
        events,
        workflow_executor=executor,
        event_fire_callback=event_callback,
    )
