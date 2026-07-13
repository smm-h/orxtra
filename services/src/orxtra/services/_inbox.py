from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.trace import InboxItem, TraceWriter
from orxtra.trace import read_inbox as _read_inbox
from orxtra.trace import read_inbox_item as _read_inbox_item

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxtra.protocols import Principal


async def list_inbox(
    pool: asyncpg.Pool, run_id: UUID, status: str | None = None
) -> list[InboxItem]:
    return await _read_inbox(pool, run_id, status)


async def get_inbox_item(pool: asyncpg.Pool, item_id: UUID) -> InboxItem:
    result = await _read_inbox_item(pool, item_id)
    if result is None:
        msg = f"inbox item {item_id} not found"
        raise ValueError(msg)
    return result


async def respond_to_inbox(
    pool: asyncpg.Pool,
    caller_principal: Principal,
    *,
    item_id: UUID,
    answer: str,
) -> InboxItem:
    writer = TraceWriter(pool)
    await writer.answer_inbox_item(
        item_id, answer, resolved_by=caller_principal.id,
    )
    return await get_inbox_item(pool, item_id)


async def skip_inbox_item(
    pool: asyncpg.Pool,
    caller_principal: Principal,
    *,
    item_id: UUID,
) -> InboxItem:
    writer = TraceWriter(pool)
    await writer.skip_inbox_item(item_id, resolved_by=caller_principal.id)
    return await get_inbox_item(pool, item_id)


async def reject_inbox_item(
    pool: asyncpg.Pool,
    caller_principal: Principal,
    *,
    item_id: UUID,
    reason: str,
) -> InboxItem:
    writer = TraceWriter(pool)
    await writer.reject_inbox_item(
        item_id, reason, resolved_by=caller_principal.id,
    )
    return await get_inbox_item(pool, item_id)
