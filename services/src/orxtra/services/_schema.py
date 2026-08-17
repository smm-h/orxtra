"""Shared schema verification and asyncpg adapter for the generated executor.

Provides:
- ``AsyncpgAdapter`` / ``AsyncpgTx``: bridge between asyncpg's native types
  and the generated schema executor's ``AsyncConnection`` / ``AsyncTransaction``
  protocols. Previously duplicated in cli/_db.py, tests/pg_fixtures.py, and
  tests/test_db_commands.py.
- ``SchemaError``: raised when the database schema is incomplete.
- ``verify_schema(pool)``: single importable function that runs the generated
  executor's verify() and raises ``SchemaError`` with an actionable message if
  any required objects are missing. Designed for use by every long-running
  DB-backed process: ``orxtra serve``, CLI commands, dispatcher worker,
  incoming receiver.

The helper filters known false positives from verify():
- ``comments`` section: pg_catalog has no query for COMMENT ON statements.
- Functions and triggers that the executor places in the ``indexes`` section
  (the existence checker queries pg_indexes, which only has actual indexes).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Self

if TYPE_CHECKING:
    import types

    import asyncpg


# ---------------------------------------------------------------------------
# asyncpg adapter for the generated schema executor
# ---------------------------------------------------------------------------


class AsyncpgTx:
    """Adapter wrapping asyncpg transaction to satisfy AsyncTransaction."""

    def __init__(
        self,
        conn: asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any],
    ) -> None:
        self._conn = conn
        self._tx: Any = None

    async def __aenter__(self) -> Self:
        self._tx = self._conn.transaction()
        await self._tx.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: types.TracebackType | None,
    ) -> None:
        if exc_type is not None:
            await self._tx.rollback()
        else:
            await self._tx.commit()

    async def execute(self, query: str) -> None:
        await self._conn.execute(query)


class AsyncpgAdapter:
    """Adapter wrapping asyncpg.Connection to satisfy AsyncConnection."""

    def __init__(
        self,
        conn: asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any],
    ) -> None:
        self._conn = conn

    async def execute(self, query: str) -> None:
        await self._conn.execute(query)

    async def fetch(self, query: str) -> list[dict[str, Any]]:
        rows = await self._conn.fetch(query)
        return [dict(r) for r in rows]

    def transaction(self) -> AsyncpgTx:
        return AsyncpgTx(self._conn)


# ---------------------------------------------------------------------------
# Schema verification
# ---------------------------------------------------------------------------

# Sections excluded from verify() because they produce false positives:
# - comments: no pg_catalog query exists for COMMENT ON statements.
# The schema declares no extensions at all since the move to PostgreSQL 18's
# native uuidv7(), so nothing else needs excluding.
_VERIFY_EXCLUDE_SECTIONS: list[str] = ["comments"]


def _is_false_positive(kind: str, name: str) -> bool:
    """Return True if this missing entry is a known false positive.

    The generated executor places functions (pgdesign_deny_mutation) and
    triggers (*.deny_mutation) in the "indexes" section. The existence
    checker for "indexes" queries pg_indexes, which only contains actual
    indexes -- so functions and triggers always appear missing.
    """
    return kind == "indexes" and "deny_mutation" in name


class SchemaError(Exception):
    """Raised when the database schema is incomplete or outdated."""


async def ensure_schema_objects(adapter: AsyncpgAdapter) -> Any:
    """Run the generated ``ensure_schema()`` against a live connection.

    The pgdesign-generated executor is private to this package, so callers
    reach it through here rather than importing ``_generated`` across a
    package boundary. The returned value is the generated ``ExecutionResult``,
    carrying ``executed``, ``skipped`` and ``errors``.

    Args:
        adapter: An ``AsyncpgAdapter`` wrapping a live connection.

    Returns:
        The generated executor's ``ExecutionResult``.
    """
    from orxtra.services._generated.schema_executor import (
        ensure_schema,
    )

    # AsyncpgAdapter satisfies the generated AsyncConnection protocol at
    # runtime; the generated protocol mistypes transaction() as an async def
    # (coroutine) though it is used as a plain async context manager. Known
    # pgdesign generated-protocol gap (filed upstream).
    return await ensure_schema(adapter)  # type: ignore[arg-type]


async def verify_schema_objects(
    adapter: AsyncpgAdapter,
) -> tuple[list[tuple[str, str]], list[tuple[str, str]]]:
    """Run the generated verify() with known false positives filtered.

    Runs the generated executor's ``verify()`` excluding the sections and
    entries that have no reliable existence check (see ``_VERIFY_EXCLUDE_SECTIONS``
    and ``_is_false_positive``), so callers get a truthful present/missing split.

    Args:
        adapter: An ``AsyncpgAdapter`` wrapping a live connection.

    Returns:
        A ``(present, real_missing)`` tuple of ``(kind, name)`` lists.
    """
    from orxtra.services._generated.schema_executor import (
        verify,
    )

    # AsyncpgAdapter satisfies the generated AsyncConnection protocol at
    # runtime; the generated protocol mistypes transaction() as an async def
    # (coroutine) though it is used as a plain async context manager. Known
    # pgdesign generated-protocol gap (filed upstream).
    result = await verify(
        adapter,  # type: ignore[arg-type]
        exclude_sections=_VERIFY_EXCLUDE_SECTIONS,
    )
    present = [(kind, name) for kind, name in result.present]
    real_missing = [
        (kind, name)
        for kind, name in result.missing
        if not _is_false_positive(kind, name)
    ]
    return present, real_missing


async def verify_schema(pool: asyncpg.Pool[Any]) -> None:
    """Verify the database schema is complete, raise on missing objects.

    Acquires a single connection from the pool, runs the generated
    executor's verify(), filters known false positives, and raises
    ``SchemaError`` with an actionable message if real objects are missing.

    Returns silently if everything is present.

    Args:
        pool: An asyncpg connection pool connected to the target database.

    Raises:
        SchemaError: If required schema objects are missing.
    """
    async with pool.acquire() as conn:
        adapter = AsyncpgAdapter(conn)
        _present, real_missing = await verify_schema_objects(adapter)

    if real_missing:
        names = ", ".join(f"{kind}.{name}" for kind, name in real_missing)
        msg = (
            f"Database schema is incomplete. "
            f"Missing: {names}. "
            f"Run 'orxtra db init' or 'orxtra db migrate apply'."
        )
        raise SchemaError(msg)
