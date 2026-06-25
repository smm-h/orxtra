from __future__ import annotations

import asyncio

import pytest

from orxtra.dispatch import TransientEventDelivery
from orxtra.protocols import EventDelivery


@pytest.fixture
def delivery() -> TransientEventDelivery:
    return TransientEventDelivery()


async def test_fire_resolves_waiter(delivery: TransientEventDelivery) -> None:
    """wait_for + fire -> payload received."""
    payload = {"key": "value"}

    async def waiter() -> dict[str, object] | None:
        return await delivery.wait_for("evt", deadline_seconds=5.0)

    task = asyncio.create_task(waiter())
    # Yield control so the waiter registers its future.
    await asyncio.sleep(0)
    await delivery.fire("evt", payload)
    result = await task
    assert result == payload


async def test_fire_before_wait_lost(delivery: TransientEventDelivery) -> None:
    """fire then wait_for -> timeout (None), because there are no replay semantics."""
    await delivery.fire("evt", {"gone": True})
    result = await delivery.wait_for("evt", deadline_seconds=0.05)
    assert result is None


async def test_multiple_waiters(delivery: TransientEventDelivery) -> None:
    """Two wait_for calls on the same event: fire resolves both with the same payload."""
    payload = {"shared": 42}

    task_a = asyncio.create_task(
        delivery.wait_for("evt", deadline_seconds=5.0),
    )
    task_b = asyncio.create_task(
        delivery.wait_for("evt", deadline_seconds=5.0),
    )
    await asyncio.sleep(0)
    await delivery.fire("evt", payload)

    assert await task_a == payload
    assert await task_b == payload


async def test_timeout_returns_none(delivery: TransientEventDelivery) -> None:
    """wait_for with a very short deadline and no fire -> None."""
    result = await delivery.wait_for("evt", deadline_seconds=0.01)
    assert result is None


async def test_different_events_independent(
    delivery: TransientEventDelivery,
) -> None:
    """fire on event_a must not affect event_b waiter."""
    task_b = asyncio.create_task(
        delivery.wait_for("event_b", deadline_seconds=0.05),
    )
    await asyncio.sleep(0)
    await delivery.fire("event_a", {"a": 1})

    # event_b waiter should time out, unaffected.
    assert await task_b is None


async def test_satisfies_protocol() -> None:
    """TransientEventDelivery satisfies the EventDelivery protocol."""
    instance = TransientEventDelivery()
    assert isinstance(instance, EventDelivery)
