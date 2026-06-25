"""Tests for dual-phase delivery, persistent subscription matching, and filter evaluation."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from uuid6 import uuid7

from orxtra.dispatch import (
    DualPhaseEventDelivery,
    FilterPredicate,
    InMemoryDispatchBackend,
    Subscription,
    SubscriptionAction,
    match_subscription,
)
from orxtra.protocols import Action, EventDelivery, LogAction, ScriptAction

NOW = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)


# -- match_subscription tests --


class TestMatchSubscription:
    def test_empty_filter_matches_everything(self) -> None:
        f = FilterPredicate()
        assert match_subscription("task.completed", "scheduler", {"x": 1}, f)

    def test_event_types_match(self) -> None:
        f = FilterPredicate(event_types=["task.completed", "task.failed"])
        assert match_subscription("task.completed", None, None, f)
        assert match_subscription("task.failed", None, None, f)

    def test_event_types_no_match(self) -> None:
        f = FilterPredicate(event_types=["task.completed"])
        assert not match_subscription("task.started", None, None, f)

    def test_sources_match(self) -> None:
        f = FilterPredicate(sources=["scheduler", "overseer"])
        assert match_subscription("any.event", "scheduler", None, f)

    def test_sources_no_match(self) -> None:
        f = FilterPredicate(sources=["scheduler"])
        assert not match_subscription("any.event", "overseer", None, f)

    def test_sources_none_source_no_match(self) -> None:
        f = FilterPredicate(sources=["scheduler"])
        assert not match_subscription("any.event", None, None, f)

    def test_combined_filter(self) -> None:
        f = FilterPredicate(
            event_types=["task.completed"],
            sources=["scheduler"],
        )
        assert match_subscription("task.completed", "scheduler", None, f)
        assert not match_subscription("task.failed", "scheduler", None, f)
        assert not match_subscription("task.completed", "overseer", None, f)

    def test_data_predicates_ignored(self) -> None:
        """data_predicates is reserved; currently ignored."""
        f = FilterPredicate(data_predicates={"task_name": "build"})
        assert match_subscription("any", None, None, f)


# -- DualPhaseEventDelivery tests --


def _make_sub(
    *,
    event_types: list[str] | None = None,
    sources: list[str] | None = None,
    enabled: bool = True,
) -> Subscription:
    return Subscription(
        id=uuid7(),
        filter=FilterPredicate(event_types=event_types, sources=sources),
        enabled=enabled,
        created_at=NOW,
    )


def _make_action(
    *,
    sub_id: Any,
    action: Action,
    position: int = 0,
    accumulator_config: dict[str, Any] | None = None,
) -> SubscriptionAction:
    return SubscriptionAction(
        id=uuid7(),
        subscription_id=sub_id,
        position=position,
        action=action,
        accumulator_config=accumulator_config,
        created_at=NOW,
    )


class TestDualPhaseTransientOnly:
    """When no backend is provided, dual-phase engine is transient-only."""

    async def test_fire_resolves_waiter(self) -> None:
        engine = DualPhaseEventDelivery()
        payload = {"key": "value"}

        async def waiter() -> dict[str, object] | None:
            return await engine.wait_for("evt", deadline_seconds=5.0)

        task = asyncio.create_task(waiter())
        await asyncio.sleep(0)
        await engine.fire("evt", payload)
        result = await task
        assert result == payload

    async def test_no_backend_fire_succeeds(self) -> None:
        """fire() without a backend does not raise."""
        engine = DualPhaseEventDelivery()
        await engine.fire("some.event", {"data": True})

    async def test_timeout_returns_none(self) -> None:
        engine = DualPhaseEventDelivery()
        result = await engine.wait_for("evt", deadline_seconds=0.01)
        assert result is None


class TestDualPhasePersistent:
    """Persistent phase: subscription matching + action dispatch."""

    async def test_phase_ordering_transient_first(self) -> None:
        """Transient futures are resolved (set_result) before persistent
        actions execute within fire()."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        await backend.create_action(
            _make_action(
                sub_id=sub.id,
                action=LogAction(message="persistent fired", level="info"),
            ),
        )

        engine = DualPhaseEventDelivery(backend=backend)

        # Register a waiter before firing.
        waiter = asyncio.create_task(
            engine.wait_for("evt", deadline_seconds=5.0),
        )
        await asyncio.sleep(0)

        # After fire(), the future should be resolved (transient phase)
        # AND the persistent action should have executed (no error).
        await engine.fire("evt", {"x": 1})

        # The waiter's future was set_result'd during fire() (transient phase).
        result = await waiter
        assert result == {"x": 1}

    async def test_matching_subscription_dispatches_action(self) -> None:
        """A subscription matching the event type dispatches its action."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["task.completed"])
        await backend.create_subscription(sub)

        # LogAction is the simplest to verify (no external deps).
        await backend.create_action(
            _make_action(
                sub_id=sub.id,
                action=LogAction(message="task completed log", level="info"),
            ),
        )

        engine = DualPhaseEventDelivery(backend=backend)
        # Should not raise -- the LogAction just logs.
        await engine.fire("task.completed", {"task_id": "abc"}, source="scheduler")

    async def test_non_matching_subscription_skipped(self) -> None:
        """A subscription that does not match is silently skipped."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["task.failed"])
        await backend.create_subscription(sub)

        # This ScriptAction would raise if it ran (bad callable path).
        await backend.create_action(
            _make_action(
                sub_id=sub.id,
                action=ScriptAction(callable="nonexistent.module:func"),
            ),
        )

        engine = DualPhaseEventDelivery(backend=backend)
        # Firing a different event type should not trigger the action.
        await engine.fire("task.completed", {"ok": True})

    async def test_disabled_subscription_skipped(self) -> None:
        """Disabled subscriptions are not evaluated."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"], enabled=False)
        await backend.create_subscription(sub)

        await backend.create_action(
            _make_action(
                sub_id=sub.id,
                action=ScriptAction(callable="nonexistent.module:func"),
            ),
        )

        engine = DualPhaseEventDelivery(backend=backend)
        await engine.fire("evt")  # should not raise

    async def test_source_filter(self) -> None:
        """Source filter narrows matching."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(sources=["scheduler"])
        await backend.create_subscription(sub)

        await backend.create_action(
            _make_action(
                sub_id=sub.id,
                action=ScriptAction(callable="nonexistent.module:func"),
            ),
        )

        engine = DualPhaseEventDelivery(backend=backend)
        # Wrong source -- should not trigger.
        await engine.fire("evt", source="overseer")

    async def test_multiple_subscriptions_multiple_actions(self) -> None:
        """Multiple matching subscriptions each dispatch their actions."""
        backend = InMemoryDispatchBackend()

        sub1 = _make_sub(event_types=["evt"])
        sub2 = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub1)
        await backend.create_subscription(sub2)

        await backend.create_action(
            _make_action(
                sub_id=sub1.id,
                action=LogAction(message="sub1 log", level="info"),
            ),
        )
        await backend.create_action(
            _make_action(
                sub_id=sub2.id,
                action=LogAction(message="sub2 log", level="info"),
            ),
        )

        engine = DualPhaseEventDelivery(backend=backend)
        await engine.fire("evt")  # both should fire without error

    async def test_satisfies_event_delivery_protocol(self) -> None:
        """DualPhaseEventDelivery satisfies the EventDelivery protocol
        for the fire(event_name, payload) signature."""
        engine = DualPhaseEventDelivery()
        # The protocol requires fire(event_name, payload) and
        # wait_for(event_name, deadline_seconds). DualPhaseEventDelivery's
        # fire() has source as keyword-only, so the base signature matches.
        assert isinstance(engine, EventDelivery)
