from __future__ import annotations

import json
from typing import TYPE_CHECKING
from uuid import UUID

from orxtra.dispatch._types import (
    AccumulatorEntry,
    FilterPredicate,
    Source,
    Subscription,
    SubscriptionAction,
)
from orxtra.protocols import EventAction, LogAction, ScriptAction, WorkflowAction

if TYPE_CHECKING:
    import asyncpg

    from orxtra.protocols import Action

# Map Action subclass -> DB action_type string.
_ACTION_TYPE_MAP: dict[type, str] = {
    ScriptAction: "script",
    LogAction: "log",
    WorkflowAction: "workflow",
    EventAction: "event",
}

# Reverse: DB action_type string -> Action subclass.
_ACTION_CLASS_MAP: dict[str, type] = {v: k for k, v in _ACTION_TYPE_MAP.items()}


def _serialize_action(action: Action) -> tuple[str, str]:
    """Decompose an Action into (action_type, action_config_json)."""
    action_type = _ACTION_TYPE_MAP.get(type(action))
    if action_type is None:
        msg = f"Unknown action type: {type(action).__name__}"
        raise TypeError(msg)
    return action_type, json.dumps(action.model_dump())


def _deserialize_action(action_type: str, action_config: str) -> Action:
    """Reconstruct an Action from DB columns."""
    cls = _ACTION_CLASS_MAP.get(action_type)
    if cls is None:
        msg = f"Unknown action_type in DB: {action_type!r}"
        raise ValueError(msg)
    data = json.loads(action_config) if isinstance(action_config, str) else action_config
    return cls.model_validate(data)


class PgDispatchBackend:
    """asyncpg-backed implementation of DispatchBackend."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- SourceStorage --

    async def create_source(self, source: Source) -> UUID:
        auth_config_json = (
            json.dumps(source.auth_config)
            if source.auth_config is not None
            else None
        )
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO sources"
                " (id, slug, name, auth_method, auth_config)"
                " VALUES ($1, $2, $3, $4, $5)",
                source.id,
                source.slug,
                source.name,
                source.auth_method,
                auth_config_json,
            )
        return source.id

    async def get_source(self, source_id: UUID) -> Source | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, slug, name, auth_method,"
                " auth_config, created_at"
                " FROM sources WHERE id = $1",
                source_id,
            )
        if row is None:
            return None
        return _row_to_source(row)

    async def get_source_by_slug(self, slug: str) -> Source | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, slug, name, auth_method,"
                " auth_config, created_at"
                " FROM sources WHERE slug = $1",
                slug,
            )
        if row is None:
            return None
        return _row_to_source(row)

    async def list_sources(self) -> list[Source]:
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT id, slug, name, auth_method,"
                " auth_config, created_at"
                " FROM sources ORDER BY created_at",
            )
        return [_row_to_source(r) for r in rows]

    async def delete_source(self, source_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM sources WHERE id = $1",
                source_id,
            )

    # -- SubscriptionStorage --

    async def create_subscription(self, subscription: Subscription) -> UUID:
        filter_json = json.dumps(subscription.filter.model_dump())
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO subscriptions"
                " (id, filter_expr, enabled, storage, owner_run_id)"
                " VALUES ($1, $2, $3, $4, $5)",
                subscription.id,
                filter_json,
                subscription.enabled,
                subscription.storage,
                subscription.owner_run_id,
            )
        return subscription.id

    async def get_subscription(self, sub_id: UUID) -> Subscription | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, filter_expr, enabled, storage,"
                " owner_run_id, created_at"
                " FROM subscriptions WHERE id = $1",
                sub_id,
            )
        if row is None:
            return None
        return _row_to_subscription(row)

    async def list_subscriptions(
        self, *, enabled_only: bool = True,
    ) -> list[Subscription]:
        if enabled_only:
            sql = (
                "SELECT id, filter_expr, enabled, storage,"
                " owner_run_id, created_at"
                " FROM subscriptions WHERE enabled = true"
                " ORDER BY created_at"
            )
        else:
            sql = (
                "SELECT id, filter_expr, enabled, storage,"
                " owner_run_id, created_at"
                " FROM subscriptions"
                " ORDER BY created_at"
            )
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(sql)
        return [_row_to_subscription(r) for r in rows]

    async def update_subscription(
        self, sub_id: UUID, *, enabled: bool,
    ) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            status = await conn.execute(
                "UPDATE subscriptions SET enabled = $1 WHERE id = $2",
                enabled,
                sub_id,
            )
            if status == "UPDATE 0":
                msg = f"Subscription {sub_id} not found"
                raise KeyError(msg)

    async def delete_subscription(self, sub_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM subscriptions WHERE id = $1",
                sub_id,
            )

    # -- ActionStorage --

    async def create_action(self, action: SubscriptionAction) -> UUID:
        action_type, action_config = _serialize_action(action.action)
        accum_json = (
            json.dumps(action.accumulator_config)
            if action.accumulator_config is not None
            else None
        )
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO subscription_actions"
                " (id, subscription_id, position,"
                " action_type, action_config, accumulator_config)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                action.id,
                action.subscription_id,
                action.position,
                action_type,
                action_config,
                accum_json,
            )
        return action.id

    async def list_actions(self, sub_id: UUID) -> list[SubscriptionAction]:
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT id, subscription_id, position,"
                " action_type, action_config,"
                " accumulator_config, created_at"
                " FROM subscription_actions"
                " WHERE subscription_id = $1"
                " ORDER BY position",
                sub_id,
            )
        return [_row_to_subscription_action(r) for r in rows]

    async def delete_actions(self, sub_id: UUID) -> None:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM subscription_actions"
                " WHERE subscription_id = $1",
                sub_id,
            )

    # -- AccumulatorStorage --

    async def buffer_event(self, entry: AccumulatorEntry) -> UUID:
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO accumulator_buffer"
                " (id, subscription_action_id, event_id)"
                " VALUES ($1, $2, $3)",
                entry.id,
                entry.subscription_action_id,
                entry.event_id,
            )
        return entry.id

    async def claim_batch(
        self, action_id: UUID, limit: int = 100,
    ) -> list[AccumulatorEntry]:
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT id, subscription_action_id, event_id, created_at"
                " FROM accumulator_buffer"
                " WHERE subscription_action_id = $1"
                " ORDER BY created_at"
                " LIMIT $2"
                " FOR UPDATE SKIP LOCKED",
                action_id,
                limit,
            )
            if rows:
                ids = [row["id"] for row in rows]
                await conn.execute(
                    "DELETE FROM accumulator_buffer"
                    " WHERE id = ANY($1::uuid[])",
                    ids,
                )
        return [_row_to_accumulator_entry(r) for r in rows]

    async def confirm_batch(self, entry_ids: list[UUID]) -> None:
        if not entry_ids:
            return
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "DELETE FROM accumulator_buffer"
                " WHERE id = ANY($1::uuid[])",
                entry_ids,
            )

    async def pending_count(self, action_id: UUID) -> int:
        async with self._pool.acquire() as conn, conn.transaction():
            count = await conn.fetchval(
                "SELECT count(*) FROM accumulator_buffer"
                " WHERE subscription_action_id = $1",
                action_id,
            )
        return int(count)


# -- Row -> model helpers --


def _row_to_source(row: asyncpg.Record) -> Source:
    auth_config = row["auth_config"]
    if isinstance(auth_config, str):
        auth_config = json.loads(auth_config)
    return Source(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        auth_method=row["auth_method"],
        auth_config=auth_config,
        created_at=row["created_at"],
    )


def _row_to_subscription(row: asyncpg.Record) -> Subscription:
    filter_data = row["filter_expr"]
    if isinstance(filter_data, str):
        filter_data = json.loads(filter_data)
    return Subscription(
        id=row["id"],
        filter=FilterPredicate.model_validate(filter_data),
        enabled=row["enabled"],
        storage=row["storage"],
        owner_run_id=row["owner_run_id"],
        created_at=row["created_at"],
    )


def _row_to_subscription_action(row: asyncpg.Record) -> SubscriptionAction:
    action_config = row["action_config"]
    accum = row["accumulator_config"]
    if isinstance(accum, str):
        accum = json.loads(accum)
    return SubscriptionAction(
        id=row["id"],
        subscription_id=row["subscription_id"],
        position=row["position"],
        action=_deserialize_action(row["action_type"], action_config),
        accumulator_config=accum,
        created_at=row["created_at"],
    )


def _row_to_accumulator_entry(row: asyncpg.Record) -> AccumulatorEntry:
    return AccumulatorEntry(
        id=row["id"],
        subscription_action_id=row["subscription_action_id"],
        event_id=row["event_id"],
        created_at=row["created_at"],
    )
