from __future__ import annotations

from typing import TYPE_CHECKING

from orxt.trace import InboxItem, TraceWriter
from orxt.trace import read_inbox as _read_inbox

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg


async def list_inbox(
    pool: asyncpg.Pool, run_id: UUID, status: str | None = None
) -> list[InboxItem]:
    return await _read_inbox(pool, run_id, status)


async def get_inbox_item(pool: asyncpg.Pool, item_id: UUID) -> InboxItem:
    row = await pool.fetchrow(
        "SELECT * FROM inbox_items WHERE id = $1", item_id
    )
    if row is None:
        msg = f"inbox item {item_id} not found"
        raise ValueError(msg)
    return InboxItem.model_validate(dict(row))


async def respond_to_inbox(
    pool: asyncpg.Pool, item_id: UUID, answer: str
) -> InboxItem:
    writer = TraceWriter(pool)
    await writer.answer_inbox_item(item_id, answer)
    return await get_inbox_item(pool, item_id)


async def skip_inbox_item(pool: asyncpg.Pool, item_id: UUID) -> InboxItem:
    writer = TraceWriter(pool)
    await writer.skip_inbox_item(item_id)
    return await get_inbox_item(pool, item_id)


async def reject_inbox_item(
    pool: asyncpg.Pool, item_id: UUID, reason: str
) -> InboxItem:
    writer = TraceWriter(pool)
    await writer.reject_inbox_item(item_id, reason)
    return await get_inbox_item(pool, item_id)
