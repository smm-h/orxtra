"""Unit tests for the schema adapter and false-positive filtering.

Tests that patch _generated.schema_executor (TestVerifySchema) live in
root tests/test_schema_unit.py -- they need schema/ on sys.path.
"""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from orxtra.services._schema import (
    AsyncpgAdapter,
    AsyncpgTx,
    _is_false_positive,
)

# ---------------------------------------------------------------------------
# AsyncpgAdapter / AsyncpgTx tests
# ---------------------------------------------------------------------------


class TestAsyncpgAdapter:
    """Tests for the asyncpg adapter classes."""

    async def test_adapter_execute_delegates(self) -> None:
        conn = AsyncMock()
        adapter = AsyncpgAdapter(conn)
        await adapter.execute("SELECT 1")
        conn.execute.assert_awaited_once_with("SELECT 1")

    async def test_adapter_fetch_converts_records_to_dicts(self) -> None:
        record = MagicMock()
        record.__iter__ = MagicMock(
            return_value=iter([("col", "val")]),
        )
        # asyncpg Records support dict() conversion
        conn = AsyncMock()
        conn.fetch = AsyncMock(return_value=[{"col": "val"}])
        adapter = AsyncpgAdapter(conn)
        result = await adapter.fetch("SELECT 1")
        assert result == [{"col": "val"}]

    def test_adapter_transaction_returns_tx(self) -> None:
        conn = AsyncMock()
        adapter = AsyncpgAdapter(conn)
        tx = adapter.transaction()
        assert isinstance(tx, AsyncpgTx)


# ---------------------------------------------------------------------------
# False positive filtering
# ---------------------------------------------------------------------------


class TestFalsePositiveFiltering:
    """Tests for the known-false-positive filter."""

    def test_deny_mutation_in_indexes_is_false_positive(self) -> None:
        assert _is_false_positive("indexes", "transcripts.deny_mutation")
        assert _is_false_positive("indexes", "notepad_entries.deny_mutation")
        assert _is_false_positive("indexes", "events.deny_mutation")

    def test_deny_mutation_function_is_false_positive(self) -> None:
        assert _is_false_positive(
            "indexes", "public.pgdesign_deny_mutation",
        )

    def test_real_index_is_not_false_positive(self) -> None:
        assert not _is_false_positive("indexes", "idx_runs_config_snapshot_gin")

    def test_real_table_is_not_false_positive(self) -> None:
        assert not _is_false_positive("tables", "runs")

    def test_other_kinds_are_not_false_positive(self) -> None:
        assert not _is_false_positive("tables", "deny_mutation")
        assert not _is_false_positive("types", "some_type")
