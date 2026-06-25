from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import Any

from uuid6 import uuid7

from orxtra.protocols import Action, ActionExecutor, EventFireCallback, FlushScheduler

from orxtra.dispatch._action_executor import execute_action
from orxtra.dispatch._protocols import DispatchBackend
from orxtra.dispatch._types import AccumulatorEntry, FilterPredicate

logger = logging.getLogger(__name__)


class TransientEventDelivery:
    """In-memory event delivery using asyncio Futures.

    Implements the ``EventDelivery`` protocol from ``orxtra.protocols``.
    Same semantics as the scheduler's ``EventRegistry``: fire resolves
    all current waiters, events fired before any waiter registers are
    silently lost (no replay), and multiple waiters on the same event
    all receive the same payload.
    """

    def __init__(self) -> None:
        self._listeners: dict[
            str, list[asyncio.Future[dict[str, object] | None]]
        ] = {}

    async def fire(
        self,
        event_name: str,
        payload: dict[str, object] | None = None,
        *,
        source: str | None = None,
    ) -> None:
        futures = self._listeners.pop(event_name, [])
        for fut in futures:
            if not fut.done():
                fut.set_result(payload)

    async def wait_for(
        self,
        event_name: str,
        *,
        deadline_seconds: float,
    ) -> dict[str, object] | None:
        loop = asyncio.get_running_loop()
        fut: asyncio.Future[dict[str, object] | None] = loop.create_future()
        if event_name not in self._listeners:
            self._listeners[event_name] = []
        self._listeners[event_name].append(fut)
        try:
            return await asyncio.wait_for(fut, timeout=deadline_seconds)
        except TimeoutError:
            return None


def match_subscription(
    event_type: str,
    source: str | None,
    data: dict[str, Any] | None,
    filter_predicate: FilterPredicate,
) -> bool:
    """Evaluate whether an event matches a subscription's filter.

    Filter semantics:
    - ``event_types``: if set, event_type must be in the list.
    - ``sources``: if set, source must be in the list.
    - ``data_predicates``: reserved for future jsonb matching; ignored now.
    - All None fields are treated as wildcards (match everything).
    """
    if filter_predicate.event_types is not None:
        if event_type not in filter_predicate.event_types:
            return False
    if filter_predicate.sources is not None:
        if source is None or source not in filter_predicate.sources:
            return False
    # data_predicates: reserved, not evaluated yet.
    return True


class DualPhaseEventDelivery:
    """Full dual-phase delivery engine: transient futures + persistent subscriptions.

    Phase 1 (transient): resolve in-memory futures (delegates to
    ``TransientEventDelivery``).

    Phase 2 (persistent): query ``DispatchBackend`` for matching
    subscriptions, evaluate filter predicates, and dispatch actions
    (or buffer for accumulator flush).

    Constructor accepts optional ``DispatchBackend``. When None, only
    transient delivery works (Phase 1 only).
    """

    def __init__(
        self,
        *,
        backend: DispatchBackend | None = None,
        workflow_executor: ActionExecutor | None = None,
        flush_scheduler: FlushScheduler | None = None,
        max_concurrent: int = 10,
    ) -> None:
        self._transient = TransientEventDelivery()
        self._backend = backend
        self._workflow_executor = workflow_executor
        self._flush_scheduler = flush_scheduler
        self._max_concurrent = max_concurrent

    async def fire(
        self,
        event_name: str,
        payload: dict[str, object] | None = None,
        *,
        source: str | None = None,
    ) -> None:
        """Fire an event through both phases.

        Phase 1: resolve transient futures.
        Phase 2: dispatch persistent subscription actions (if backend present).
        """
        # Phase 1: transient
        await self._transient.fire(event_name, payload)

        # Phase 2: persistent
        if self._backend is not None:
            await self._fire_persistent(event_name, payload, source)

    async def wait_for(
        self,
        event_name: str,
        *,
        deadline_seconds: float,
    ) -> dict[str, object] | None:
        """Delegate to transient delivery."""
        return await self._transient.wait_for(
            event_name, deadline_seconds=deadline_seconds,
        )

    def _make_event_fire_callback(self) -> EventFireCallback:
        """Create a callback that re-enters fire() for EventAction dispatch."""

        async def _callback(
            event_type: str,
            data: dict[str, object] | None,
        ) -> None:
            await self.fire(event_type, data, source="internal")

        return _callback

    async def _fire_persistent(
        self,
        event_type: str,
        data: dict[str, object] | None,
        source: str | None,
    ) -> None:
        """Query subscriptions, match filters, dispatch or buffer actions."""
        assert self._backend is not None  # noqa: S101 -- guarded by caller
        subscriptions = await self._backend.list_subscriptions(enabled_only=True)

        event_data: dict[str, object] = {}
        if data is not None:
            event_data.update(data)

        event_payload: dict[str, object] = {
            "event_type": event_type,
            "source": source or "",
            "data": event_data,
        }

        for sub in subscriptions:
            if not match_subscription(event_type, source, data, sub.filter):
                continue

            actions = await self._backend.list_actions(sub.id)
            for sub_action in actions:
                if sub_action.accumulator_config is not None:
                    await self._buffer_or_flush(sub_action, event_payload)
                else:
                    action = _resolve_action(sub_action.action)
                    await execute_action(
                        action,
                        [event_payload],
                        workflow_executor=self._workflow_executor,
                        event_fire_callback=self._make_event_fire_callback(),
                    )

    async def _buffer_or_flush(
        self,
        sub_action: Any,
        event_payload: dict[str, object],
    ) -> None:
        """Buffer event for accumulator; flush inline if count threshold reached."""
        assert self._backend is not None  # noqa: S101

        event_id = uuid7()
        entry = AccumulatorEntry(
            id=uuid7(),
            subscription_action_id=sub_action.id,
            event_id=event_id,
            created_at=datetime.now(tz=UTC),
        )
        await self._backend.buffer_event(entry)

        config = sub_action.accumulator_config or {}
        threshold = config.get("threshold", 0)
        flush_interval_s = config.get("flush_interval_s", 0)

        pending = await self._backend.pending_count(sub_action.id)

        if threshold > 0 and pending >= threshold:
            # Inline flush: claim and execute immediately.
            await self._flush_action(sub_action)
        elif flush_interval_s > 0 and self._flush_scheduler is not None:
            # Schedule a deferred flush.
            self._flush_scheduler.schedule_flush(
                flush_interval_s,
                self._make_flush_callback(sub_action),
            )

    def _make_flush_callback(self, sub_action: Any) -> Any:
        """Create a zero-arg async callback for FlushScheduler."""

        async def _flush() -> None:
            await self._flush_action(sub_action)

        return _flush

    async def _flush_action(self, sub_action: Any) -> None:
        """Claim a batch, execute the action, confirm the batch."""
        assert self._backend is not None  # noqa: S101

        batch = await self._backend.claim_batch(sub_action.id)
        if not batch:
            return

        action = _resolve_action(sub_action.action)
        events: list[dict[str, object]] = [
            {
                "entry_id": str(entry.id),
                "event_id": str(entry.event_id),
                "created_at": entry.created_at.isoformat(),
            }
            for entry in batch
        ]
        await execute_action(
            action,
            events,
            workflow_executor=self._workflow_executor,
            event_fire_callback=self._make_event_fire_callback(),
        )
        await self._backend.confirm_batch([entry.id for entry in batch])


def _resolve_action(action_data: Any) -> Action:
    """Resolve an action dict or Action instance to an Action.

    SubscriptionAction.action is typed as Any because the dispatch
    types module cannot import from protocols without creating a
    circular dependency at the type level. At runtime, the value is
    either an Action instance or a plain dict matching one of the
    Action union members.
    """
    from orxtra.protocols import (
        EventAction,
        LogAction,
        ScriptAction,
        WorkflowAction,
    )

    if isinstance(action_data, ScriptAction | LogAction | WorkflowAction | EventAction):
        return action_data

    if not isinstance(action_data, dict):
        msg = f"Cannot resolve action from {type(action_data).__name__}"
        raise TypeError(msg)

    # Detect type from dict keys.
    if "callable" in action_data:
        return ScriptAction.model_validate(action_data)
    if "message" in action_data:
        return LogAction.model_validate(action_data)
    if "workflow_path" in action_data:
        return WorkflowAction.model_validate(action_data)
    if "event_type" in action_data:
        return EventAction.model_validate(action_data)

    msg = f"Cannot determine action type from keys: {set(action_data.keys())}"
    raise ValueError(msg)
