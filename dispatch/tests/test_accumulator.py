"""Tests for accumulator: buffer-claim-confirm cycle, count threshold inline flush, FlushScheduler integration."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from uuid6 import uuid7

from _handlers import flush_calls
from orxtra.dispatch import (
    DualPhaseEventDelivery,
    FilterPredicate,
    InMemoryDispatchBackend,
    Subscription,
    SubscriptionAction,
)

NOW = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)


# -- Helpers --


class FakeFlushScheduler:
    """Records schedule_flush and cancel_flush calls."""

    def __init__(self) -> None:
        self.scheduled: list[tuple[float, Callable[[], Awaitable[None]]]] = []
        self.cancelled: list[object] = []
        self._next_handle = 0

    def schedule_flush(
        self,
        deadline: float,
        callback: Callable[[], Awaitable[None]],
    ) -> object:
        handle = self._next_handle
        self._next_handle += 1
        self.scheduled.append((deadline, callback))
        return handle

    def cancel_flush(self, handle: object) -> None:
        self.cancelled.append(handle)


def _make_sub(
    *,
    event_types: list[str] | None = None,
) -> Subscription:
    return Subscription(
        id=uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=True,
        created_at=NOW,
    )


def _make_action(
    *,
    sub_id: Any,
    action: dict[str, Any],
    accumulator_config: dict[str, Any] | None = None,
    position: int = 0,
) -> SubscriptionAction:
    return SubscriptionAction(
        id=uuid7(),
        subscription_id=sub_id,
        position=position,
        action=action,
        accumulator_config=accumulator_config,
        created_at=NOW,
    )


@pytest.fixture(autouse=True)
def _clear_handler_calls() -> None:
    flush_calls.clear()


# -- Tests --


class TestAccumulatorBuffering:
    """Events with accumulator_config are buffered, not dispatched immediately."""

    async def test_event_buffered_not_dispatched(self) -> None:
        """When accumulator_config is set and threshold not reached, no action fires."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 5, "flush_interval_s": 0},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(backend=backend)
        await engine.fire("evt", {"x": 1})

        # The handler should NOT have been called (threshold=5, only 1 event buffered).
        assert len(flush_calls) == 0
        # But the event should be in the accumulator.
        count = await backend.pending_count(action.id)
        assert count == 1


class TestCountThresholdFlush:
    """When pending_count >= threshold, flush happens inline."""

    async def test_inline_flush_at_threshold(self) -> None:
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 3, "flush_interval_s": 0},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(backend=backend)

        # Fire 2 events -- no flush yet.
        await engine.fire("evt", {"i": 1})
        await engine.fire("evt", {"i": 2})
        assert len(flush_calls) == 0
        assert await backend.pending_count(action.id) == 2

        # Fire 3rd event -- threshold reached, inline flush.
        await engine.fire("evt", {"i": 3})
        assert len(flush_calls) == 1
        # After flush, buffer is confirmed (empty).
        assert await backend.pending_count(action.id) == 0

    async def test_flush_receives_batch_entries(self) -> None:
        """The flush handler receives entries from the claimed batch."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 2, "flush_interval_s": 0},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(backend=backend)
        await engine.fire("evt", {"i": 1})
        await engine.fire("evt", {"i": 2})

        assert len(flush_calls) == 1
        batch = flush_calls[0]
        assert len(batch) == 2
        # Each entry has entry_id, event_id, created_at.
        for entry in batch:
            assert "entry_id" in entry
            assert "event_id" in entry
            assert "created_at" in entry

    async def test_threshold_one_flushes_every_event(self) -> None:
        """Threshold=1 means every event triggers an immediate flush."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 1, "flush_interval_s": 0},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(backend=backend)
        await engine.fire("evt", {"i": 1})
        await engine.fire("evt", {"i": 2})

        assert len(flush_calls) == 2
        assert await backend.pending_count(action.id) == 0


class TestFlushSchedulerIntegration:
    """FlushScheduler is called when accumulator_config has flush_interval_s."""

    async def test_schedule_called_with_deadline(self) -> None:
        backend = InMemoryDispatchBackend()
        scheduler = FakeFlushScheduler()

        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 0, "flush_interval_s": 60},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(
            backend=backend,
            flush_scheduler=scheduler,
        )
        await engine.fire("evt", {"x": 1})

        # The handler should NOT have been called (threshold=0 means never inline).
        assert len(flush_calls) == 0
        # But the scheduler should have been called.
        assert len(scheduler.scheduled) == 1
        deadline, callback = scheduler.scheduled[0]
        assert deadline == 60

    async def test_scheduled_callback_flushes(self) -> None:
        """Executing the scheduled callback performs the flush."""
        backend = InMemoryDispatchBackend()
        scheduler = FakeFlushScheduler()

        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 0, "flush_interval_s": 30},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(
            backend=backend,
            flush_scheduler=scheduler,
        )
        await engine.fire("evt", {"x": 1})
        await engine.fire("evt", {"x": 2})

        assert await backend.pending_count(action.id) == 2

        # Simulate the scheduler firing the callback.
        _, callback = scheduler.scheduled[0]
        await callback()

        assert len(flush_calls) == 1
        assert len(flush_calls[0]) == 2
        assert await backend.pending_count(action.id) == 0

    async def test_no_scheduler_no_time_flush(self) -> None:
        """Without a FlushScheduler, flush_interval_s is ignored (no crash)."""
        backend = InMemoryDispatchBackend()

        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 0, "flush_interval_s": 60},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(backend=backend)
        # No scheduler injected. Should not crash, but won't schedule.
        await engine.fire("evt", {"x": 1})
        assert len(flush_calls) == 0
        # Event is still buffered.
        assert await backend.pending_count(action.id) == 1


class TestBufferClaimConfirmCycle:
    """End-to-end buffer -> claim -> confirm cycle via the backend directly."""

    async def test_full_cycle(self) -> None:
        """Buffer events, claim a batch, confirm it, verify empty."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 10, "flush_interval_s": 0},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(backend=backend)

        # Buffer 3 events (below threshold=10).
        for i in range(3):
            await engine.fire("evt", {"i": i})

        assert await backend.pending_count(action.id) == 3
        assert len(flush_calls) == 0

        # Manual claim + confirm cycle.
        batch = await backend.claim_batch(action.id)
        assert len(batch) == 3

        # Confirm all.
        await backend.confirm_batch([e.id for e in batch])
        assert await backend.pending_count(action.id) == 0

    async def test_flush_empty_buffer_is_noop(self) -> None:
        """Flushing when no events are buffered does nothing."""
        backend = InMemoryDispatchBackend()
        sub = _make_sub(event_types=["evt"])
        await backend.create_subscription(sub)

        action = _make_action(
            sub_id=sub.id,
            action={"callable": "_handlers:flush_handler"},
            accumulator_config={"threshold": 0, "flush_interval_s": 30},
        )
        await backend.create_action(action)

        engine = DualPhaseEventDelivery(
            backend=backend,
            flush_scheduler=FakeFlushScheduler(),
        )

        # Manually trigger the flush method on an empty buffer.
        await engine._flush_action(action)
        assert len(flush_calls) == 0
