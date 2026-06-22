from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
import uuid6
from conftest import MockPool
from orxtra.overseer._memory import (
    query_assumptions,
    query_constraints,
    query_decisions,
    query_lessons,
    query_workflow_status,
)


def _make_row(**kwargs: Any) -> dict[str, Any]:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "id": uuid6.uuid7(),
        "created_at": datetime.now(tz=UTC),
    }
    defaults.update(kwargs)
    return defaults


@pytest.mark.asyncio
async def test_query_decisions_returns_results() -> None:
    rows = [
        _make_row(
            decision_type="arch",
            choice="modular",
            rationale="clarity",
        ),
    ]
    pool = MockPool(rows)
    results = await query_decisions(pool, uuid6.uuid7(), limit=10)
    assert len(results) == 1
    assert results[0]["decision_type"] == "arch"


@pytest.mark.asyncio
async def test_query_constraints_active_only() -> None:
    rows = [
        _make_row(
            text="constraint",
            tier="mechanical",
            active=True,
        ),
    ]
    pool = MockPool(rows)
    results = await query_constraints(
        pool, uuid6.uuid7(), active_only=True,
    )
    assert len(results) == 1
    assert results[0]["active"] is True


@pytest.mark.asyncio
async def test_query_assumptions_with_status() -> None:
    rows = [
        _make_row(
            text="assumption",
            status="pending",
            scope="task",
            inbox_item_id=None,
        ),
    ]
    pool = MockPool(rows)
    results = await query_assumptions(
        pool, uuid6.uuid7(), status="pending",
    )
    assert len(results) == 1
    assert results[0]["status"] == "pending"


@pytest.mark.asyncio
async def test_query_lessons_with_tags() -> None:
    rows = [
        _make_row(
            text="lesson",
            relevance_tags='["python"]',
            permanent=True,
            source_file=None,
        ),
    ]
    pool = MockPool(rows)
    results = await query_lessons(pool, tags=["python"])
    assert len(results) == 1


@pytest.mark.asyncio
async def test_query_lessons_permanent_only() -> None:
    rows = [
        _make_row(
            text="permanent lesson",
            relevance_tags='["general"]',
            permanent=True,
            source_file=None,
        ),
    ]
    pool = MockPool(rows)
    results = await query_lessons(pool, permanent_only=True)
    assert len(results) == 1
    assert results[0]["permanent"] is True


@pytest.mark.asyncio
async def test_query_workflow_status_not_found() -> None:
    pool = MockPool([])
    result = await query_workflow_status(pool, uuid6.uuid7())
    assert result is None
