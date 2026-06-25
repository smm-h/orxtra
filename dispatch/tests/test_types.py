from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from pydantic import ValidationError

from orxtra.dispatch import (
    AccumulatorEntry,
    FilterPredicate,
    Subscription,
    SubscriptionAction,
)

NOW = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)
SUB_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
ACTION_ID = UUID("abcdef01-2345-6789-abcd-ef0123456789")
EVENT_ID = UUID("11111111-2222-3333-4444-555555555555")


class TestFilterPredicate:
    def test_empty_filter(self) -> None:
        f = FilterPredicate()
        assert f.event_types is None
        assert f.sources is None
        assert f.data_predicates is None

    def test_event_types_filter(self) -> None:
        f = FilterPredicate(event_types=["task.completed", "task.failed"])
        assert f.event_types == ["task.completed", "task.failed"]

    def test_sources_filter(self) -> None:
        f = FilterPredicate(sources=["scheduler", "overseer"])
        assert f.sources == ["scheduler", "overseer"]

    def test_frozen(self) -> None:
        f = FilterPredicate()
        with pytest.raises(ValidationError):
            f.event_types = ["x"]  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            FilterPredicate(unknown_field="x")  # type: ignore[call-arg]


class TestSubscription:
    def test_minimal(self) -> None:
        sub = Subscription(
            id=SUB_ID,
            filter=FilterPredicate(),
            created_at=NOW,
        )
        assert sub.id == SUB_ID
        assert sub.enabled is True
        assert sub.storage == "persistent"
        assert sub.owner_run_id is None

    def test_full(self) -> None:
        run_id = UUID("99999999-8888-7777-6666-555544443333")
        sub = Subscription(
            id=SUB_ID,
            filter=FilterPredicate(event_types=["task.completed"]),
            enabled=False,
            storage="transient",
            owner_run_id=run_id,
            created_at=NOW,
        )
        assert sub.enabled is False
        assert sub.storage == "transient"
        assert sub.owner_run_id == run_id

    def test_frozen(self) -> None:
        sub = Subscription(
            id=SUB_ID,
            filter=FilterPredicate(),
            created_at=NOW,
        )
        with pytest.raises(ValidationError):
            sub.enabled = False  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            Subscription(
                id=SUB_ID,
                filter=FilterPredicate(),
                created_at=NOW,
                extra_field="x",  # type: ignore[call-arg]
            )


class TestSubscriptionAction:
    def test_construction(self) -> None:
        sa = SubscriptionAction(
            id=ACTION_ID,
            subscription_id=SUB_ID,
            position=0,
            action={"callable": "my.module:handler"},
            created_at=NOW,
        )
        assert sa.id == ACTION_ID
        assert sa.subscription_id == SUB_ID
        assert sa.position == 0
        assert sa.accumulator_config is None

    def test_with_accumulator_config(self) -> None:
        sa = SubscriptionAction(
            id=ACTION_ID,
            subscription_id=SUB_ID,
            position=1,
            action={"callable": "my.module:handler"},
            accumulator_config={"batch_size": 10, "flush_interval_s": 60},
            created_at=NOW,
        )
        assert sa.accumulator_config == {"batch_size": 10, "flush_interval_s": 60}

    def test_frozen(self) -> None:
        sa = SubscriptionAction(
            id=ACTION_ID,
            subscription_id=SUB_ID,
            position=0,
            action={"callable": "my.module:handler"},
            created_at=NOW,
        )
        with pytest.raises(ValidationError):
            sa.position = 5  # type: ignore[misc]


class TestAccumulatorEntry:
    def test_construction(self) -> None:
        entry = AccumulatorEntry(
            id=EVENT_ID,
            subscription_action_id=ACTION_ID,
            event_id=UUID("22222222-3333-4444-5555-666666666666"),
            created_at=NOW,
        )
        assert entry.id == EVENT_ID
        assert entry.subscription_action_id == ACTION_ID

    def test_frozen(self) -> None:
        entry = AccumulatorEntry(
            id=EVENT_ID,
            subscription_action_id=ACTION_ID,
            event_id=UUID("22222222-3333-4444-5555-666666666666"),
            created_at=NOW,
        )
        with pytest.raises(ValidationError):
            entry.event_id = UUID("00000000-0000-0000-0000-000000000000")  # type: ignore[misc]

    def test_extra_forbid(self) -> None:
        with pytest.raises(ValidationError):
            AccumulatorEntry(
                id=EVENT_ID,
                subscription_action_id=ACTION_ID,
                event_id=UUID("22222222-3333-4444-5555-666666666666"),
                created_at=NOW,
                stale=True,  # type: ignore[call-arg]
            )
