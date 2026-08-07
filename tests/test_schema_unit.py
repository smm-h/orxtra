"""Unit tests for verify_schema() using mocked asyncpg pools.

Relocated from services/tests/test_schema.py: these tests patch
orxtra.services._generated.schema_executor.verify, which now resolves via
the orxtra namespace package (no sys.path manipulation needed). They cannot
run under --rootdir services.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.services._schema import (
    SchemaError,
    verify_schema,
)


def _make_mock_pool() -> Any:
    """Create a mock asyncpg pool with acquire() context manager."""
    pool = AsyncMock()
    conn = AsyncMock()
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire = MagicMock(return_value=ctx)
    return pool


class TestVerifySchema:
    """Tests for the verify_schema() function."""

    async def test_returns_silently_when_schema_complete(self) -> None:
        """verify_schema returns None when no real missing objects."""
        mock_verify_result = MagicMock()
        mock_verify_result.missing = []

        pool = _make_mock_pool()

        with patch(
            "orxtra.services._generated.schema_executor.verify",
            new_callable=AsyncMock,
            return_value=mock_verify_result,
        ):
            # Should not raise
            await verify_schema(pool)

    async def test_raises_schema_error_on_missing_objects(self) -> None:
        """verify_schema raises SchemaError with actionable message."""
        mock_verify_result = MagicMock()
        mock_verify_result.missing = [
            ("tables", "runs"),
            ("tables", "tasks"),
        ]

        pool = _make_mock_pool()

        with patch(
            "orxtra.services._generated.schema_executor.verify",
            new_callable=AsyncMock,
            return_value=mock_verify_result,
        ):
            with pytest.raises(SchemaError) as exc_info:
                await verify_schema(pool)

            msg = str(exc_info.value)
            assert "Database schema is incomplete" in msg
            assert "tables.runs" in msg
            assert "tables.tasks" in msg
            assert "orxtra db init" in msg
            assert "orxtra db migrate apply" in msg

    async def test_filters_false_positives(self) -> None:
        """verify_schema ignores known false positives."""
        mock_verify_result = MagicMock()
        # Only false positives in the missing list
        mock_verify_result.missing = [
            ("indexes", "transcripts.deny_mutation"),
            ("indexes", "notepad_entries.deny_mutation"),
            ("indexes", "events.deny_mutation"),
            ("indexes", "public.pgdesign_deny_mutation"),
        ]

        pool = _make_mock_pool()

        with patch(
            "orxtra.services._generated.schema_executor.verify",
            new_callable=AsyncMock,
            return_value=mock_verify_result,
        ):
            # Should not raise -- all missing items are false positives
            await verify_schema(pool)

    async def test_raises_when_real_missing_mixed_with_false_positives(
        self,
    ) -> None:
        """verify_schema raises even when false positives are mixed in."""
        mock_verify_result = MagicMock()
        mock_verify_result.missing = [
            ("indexes", "transcripts.deny_mutation"),  # false positive
            ("tables", "events"),  # real missing
        ]

        pool = _make_mock_pool()

        with patch(
            "orxtra.services._generated.schema_executor.verify",
            new_callable=AsyncMock,
            return_value=mock_verify_result,
        ):
            with pytest.raises(SchemaError) as exc_info:
                await verify_schema(pool)

            msg = str(exc_info.value)
            assert "tables.events" in msg
            # False positive should not appear in the error
            assert "deny_mutation" not in msg

    async def test_excludes_comments_section_only(self) -> None:
        """verify_schema excludes only the comments section.

        The ``extensions`` exclusion is gone with the extension itself: the
        schema declares no extensions since primary keys moved to PostgreSQL
        18's native ``uuidv7()``, and the generated executor no longer has an
        ``extensions`` section to exclude.
        """
        mock_verify_result = MagicMock()
        mock_verify_result.missing = []

        pool = _make_mock_pool()

        with patch(
            "orxtra.services._generated.schema_executor.verify",
            new_callable=AsyncMock,
            return_value=mock_verify_result,
        ) as mock_verify:
            await verify_schema(pool)
            mock_verify.assert_awaited_once()
            call_kwargs = mock_verify.call_args[1]
            assert "exclude_sections" in call_kwargs
            assert call_kwargs["exclude_sections"] == ["comments"]
