"""Tests for match_subscription filter evaluation.

The match_subscription function is shared between the DispatchWorker and
any code that evaluates subscription filters. Tests here exercise the
function in isolation (no worker lifecycle, no backend state).
"""

from __future__ import annotations

from typing import Any

from orxtra.dispatch import (
    FilterPredicate,
    match_subscription,
)
from uuid6 import uuid7

# Source principals used across the matching tests. The resolver maps the
# authoring slugs ("scheduler", "overseer") to these principal ids; events
# carry principal_id, and matching compares principal ids, not slugs.
P_SCHEDULER = uuid7()
P_OVERSEER = uuid7()
_SLUG_TO_PRINCIPAL = {"scheduler": P_SCHEDULER, "overseer": P_OVERSEER}


async def _resolve_sources(slugs: Any) -> set[Any]:
    return {_SLUG_TO_PRINCIPAL[s] for s in slugs if s in _SLUG_TO_PRINCIPAL}


class TestMatchSubscription:
    async def test_empty_filter_matches_everything(self) -> None:
        f = FilterPredicate()
        assert await match_subscription(
            "task.completed", P_SCHEDULER, {"x": 1}, f, _resolve_sources,
        )

    async def test_event_types_match(self) -> None:
        f = FilterPredicate(event_types=["task.completed", "task.failed"])
        assert await match_subscription(
            "task.completed", None, None, f, _resolve_sources,
        )
        assert await match_subscription(
            "task.failed", None, None, f, _resolve_sources,
        )

    async def test_event_types_no_match(self) -> None:
        f = FilterPredicate(event_types=["task.completed"])
        assert not await match_subscription(
            "task.started", None, None, f, _resolve_sources,
        )

    async def test_sources_match(self) -> None:
        f = FilterPredicate(sources=["scheduler", "overseer"])
        assert await match_subscription(
            "any.event", P_SCHEDULER, None, f, _resolve_sources,
        )

    async def test_sources_no_match(self) -> None:
        f = FilterPredicate(sources=["scheduler"])
        assert not await match_subscription(
            "any.event", P_OVERSEER, None, f, _resolve_sources,
        )

    async def test_sources_none_principal_no_match(self) -> None:
        f = FilterPredicate(sources=["scheduler"])
        assert not await match_subscription(
            "any.event", None, None, f, _resolve_sources,
        )

    async def test_combined_filter(self) -> None:
        f = FilterPredicate(
            event_types=["task.completed"],
            sources=["scheduler"],
        )
        assert await match_subscription(
            "task.completed", P_SCHEDULER, None, f, _resolve_sources,
        )
        assert not await match_subscription(
            "task.failed", P_SCHEDULER, None, f, _resolve_sources,
        )
        assert not await match_subscription(
            "task.completed", P_OVERSEER, None, f, _resolve_sources,
        )

    async def test_data_predicates_ignored(self) -> None:
        """data_predicates is reserved; currently ignored."""
        f = FilterPredicate(data_predicates={"task_name": "build"})
        assert await match_subscription(
            "any", None, None, f, _resolve_sources,
        )

    # -- principal_id filter --

    async def test_principal_id_match(self) -> None:
        """Event principal matches the filter's principal_id -> True."""
        target = uuid7()
        f = FilterPredicate(principal_id=target)
        assert await match_subscription(
            "any.event", target, None, f, _resolve_sources,
        )

    async def test_principal_id_no_match(self) -> None:
        """Event principal differs from the filter's principal_id -> False."""
        target = uuid7()
        other = uuid7()
        f = FilterPredicate(principal_id=target)
        assert not await match_subscription(
            "any.event", other, None, f, _resolve_sources,
        )

    async def test_principal_id_none_wildcard(self) -> None:
        """Filter principal_id=None (wildcard) -> any event principal passes."""
        f = FilterPredicate(principal_id=None)
        assert await match_subscription(
            "any.event", uuid7(), None, f, _resolve_sources,
        )
        assert await match_subscription(
            "any.event", None, None, f, _resolve_sources,
        )

    async def test_principal_id_combined_with_event_types(self) -> None:
        """Both principal_id and event_types must match (AND semantics)."""
        target = uuid7()
        f = FilterPredicate(
            event_types=["task.completed"],
            principal_id=target,
        )
        # Both match.
        assert await match_subscription(
            "task.completed", target, None, f, _resolve_sources,
        )
        # event_type matches, principal_id doesn't.
        assert not await match_subscription(
            "task.completed", uuid7(), None, f, _resolve_sources,
        )
        # principal_id matches, event_type doesn't.
        assert not await match_subscription(
            "task.failed", target, None, f, _resolve_sources,
        )

    async def test_principal_id_event_none_no_match(self) -> None:
        """Filter has principal_id set, but event has None -> False."""
        target = uuid7()
        f = FilterPredicate(principal_id=target)
        assert not await match_subscription(
            "any.event", None, None, f, _resolve_sources,
        )
