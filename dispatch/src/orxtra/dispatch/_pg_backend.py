from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast
from uuid import UUID

from orxtra.dispatch._types import (
    AccumulatorEntry,
    FilterPredicate,
    Source,
    Subscription,
    SubscriptionAction,
)
from orxtra.protocols import (
    EventAction,
    LogAction,
    NotifyAction,
    ScriptAction,
    WorkflowAction,
)

if TYPE_CHECKING:
    import asyncpg
    from orxtra.protocols import Action

type _ActionType = type[
    ScriptAction | LogAction | WorkflowAction | EventAction | NotifyAction
]

# Map Action subclass -> DB action_type string.
_ACTION_TYPE_MAP: dict[_ActionType, str] = {
    ScriptAction: "script",
    LogAction: "log",
    WorkflowAction: "workflow",
    EventAction: "event",
    NotifyAction: "notify",
}

# Reverse: DB action_type string -> Action subclass.
_ACTION_CLASS_MAP: dict[str, _ActionType] = {v: k for k, v in _ACTION_TYPE_MAP.items()}


def _serialize_action(
    action: (
        ScriptAction | LogAction | WorkflowAction
        | EventAction | NotifyAction
    ),
) -> tuple[str, str]:
    """Decompose an Action into (action_type, action_config_json)."""
    action_type = _ACTION_TYPE_MAP.get(type(action))
    if action_type is None:
        msg = f"Unknown action type: {type(action).__name__}"
        raise TypeError(msg)
    return action_type, json.dumps(action.model_dump(mode="json"))


def _deserialize_action(action_type: str, action_config: str) -> Action:
    """Reconstruct an Action from DB columns."""
    cls = _ACTION_CLASS_MAP.get(action_type)
    if cls is None:
        msg = f"Unknown action_type in DB: {action_type!r}"
        raise ValueError(msg)
    data = (
        json.loads(action_config) if isinstance(action_config, str) else action_config
    )
    # strict=False: UUIDs arrive as strings from JSON; coerce str -> UUID.
    return cls.model_validate(data, strict=False)


class PgDispatchBackend:
    """asyncpg-backed implementation of DispatchBackend."""

    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    # -- SourceStorage --

    async def create_source(self, source: Source) -> UUID:
        config_json = json.dumps(source.config) if source.config is not None else None
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO sources"
                " (id, slug, name, credential_id, config, created_by)"
                " VALUES ($1, $2, $3, $4, $5, $6)",
                source.id,
                source.slug,
                source.name,
                source.credential_id,
                config_json,
                source.created_by,
            )
        return source.id

    async def get_source(self, source_id: UUID) -> Source | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, slug, name, credential_id,"
                " config, created_by, created_at"
                " FROM sources WHERE id = $1",
                source_id,
            )
        if row is None:
            return None
        return _row_to_source(row)

    async def get_source_by_slug(self, slug: str) -> Source | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, slug, name, credential_id,"
                " config, created_by, created_at"
                " FROM sources WHERE slug = $1",
                slug,
            )
        if row is None:
            return None
        return _row_to_source(row)

    async def list_sources(self) -> list[Source]:
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                "SELECT id, slug, name, credential_id,"
                " config, created_by, created_at"
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
        filter_json = json.dumps(subscription.filter.model_dump(mode="json"))
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO subscriptions"
                " (id, filter_expr, enabled, storage, principal_id)"
                " VALUES ($1, $2, $3, $4, $5)",
                subscription.id,
                filter_json,
                subscription.enabled,
                subscription.storage,
                subscription.principal_id,
            )
        return subscription.id

    async def get_subscription(self, sub_id: UUID) -> Subscription | None:
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                "SELECT id, filter_expr, enabled, storage,"
                " principal_id, created_at"
                " FROM subscriptions WHERE id = $1",
                sub_id,
            )
        if row is None:
            return None
        return _row_to_subscription(row)

    async def list_subscriptions(
        self, *, enabled_only: bool = True, principal_id: UUID | None = None,
    ) -> list[Subscription]:
        clauses: list[str] = []
        args: list[object] = []
        if enabled_only:
            clauses.append("enabled = true")
        if principal_id is not None:
            args.append(principal_id)
            clauses.append(f"principal_id = ${len(args)}")
        # Only static column names and positional placeholders ($n) are
        # interpolated into `where`; the principal_id value is bound via `args`.
        where = f" WHERE {' AND '.join(clauses)}" if clauses else ""
        sql = (
            "SELECT id, filter_expr, enabled, storage, principal_id, created_at"  # noqa: S608
            f" FROM subscriptions{where}"
            " ORDER BY created_at"
        )
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(sql, *args)
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

    # -- CursorStorage --

    async def get_cursor_position(
        self, cursor_name: str,
    ) -> UUID | None:
        """Return the last_processed_event_id for *cursor_name*, or None."""
        async with self._pool.acquire() as conn:
            return cast(
                "UUID | None",
                await conn.fetchval(
                    "SELECT last_processed_event_id"
                    " FROM dispatch_cursor WHERE cursor_name = $1",
                    cursor_name,
                ),
            )

    async def advance_cursor(
        self, cursor_name: str, event_id: UUID,
    ) -> None:
        """Upsert the cursor position for *cursor_name*."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO dispatch_cursor"
                " (id, cursor_name, last_processed_event_id, last_processed_at)"
                " VALUES (uuidv7(), $1, $2, now())"
                " ON CONFLICT (cursor_name)"
                " DO UPDATE SET last_processed_event_id = $2,"
                "   last_processed_at = now()",
                cursor_name,
                event_id,
            )

    # -- CompletionStorage --

    async def is_action_completed(
        self, event_id: UUID, action_id: UUID,
    ) -> bool:
        """Return True if a completion record exists for this event+action."""
        async with self._pool.acquire() as conn:
            count = await conn.fetchval(
                "SELECT count(*) FROM dispatch_completions"
                " WHERE event_id = $1 AND subscription_action_id = $2",
                event_id,
                action_id,
            )
        return int(count) > 0

    async def record_completion(
        self,
        event_id: UUID,
        action_id: UUID,
        result_status: str,
    ) -> None:
        """Insert a completion record (idempotent via unique constraint)."""
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "INSERT INTO dispatch_completions"
                " (id, event_id, subscription_action_id,"
                "  result_status, completed_at)"
                " VALUES (uuidv7(), $1, $2, $3, now())"
                " ON CONFLICT (event_id, subscription_action_id)"
                " DO NOTHING",
                event_id,
                action_id,
                result_status,
            )

    # -- Event polling --

    async def poll_events_since(
        self,
        since_id: UUID | None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Return events after *since_id*, ordered by id (UUIDv7 = time-ordered).

        Each row is returned as a dict with keys:
        id, run_id, task_id, event_type, principal_id, data, idempotency_key,
        created_at.
        """
        if since_id is not None:
            query = (
                "SELECT id, run_id, task_id, event_type, principal_id,"
                " data, idempotency_key, created_at"
                " FROM events WHERE id > $1"
                " ORDER BY id LIMIT $2"
            )
            params: list[Any] = [since_id, limit]
        else:
            query = (
                "SELECT id, run_id, task_id, event_type, principal_id,"
                " data, idempotency_key, created_at"
                " FROM events ORDER BY id LIMIT $1"
            )
            params = [limit]
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [dict(r) for r in rows]


# -- Row -> model helpers --


def _row_to_source(row: asyncpg.Record) -> Source:
    config_raw: str | dict[str, Any] | None = row["config"]
    config: dict[str, Any] | None = None
    if config_raw is not None:
        config = json.loads(config_raw) if isinstance(config_raw, str) else config_raw
    return Source(
        id=row["id"],
        slug=row["slug"],
        name=row["name"],
        credential_id=row["credential_id"],
        config=config,
        created_by=row["created_by"],
        created_at=row["created_at"],
    )


def _row_to_subscription(row: asyncpg.Record) -> Subscription:
    filter_data = row["filter_expr"]
    if isinstance(filter_data, str):
        filter_data = json.loads(filter_data)
    # filter_expr is jsonb; UUIDs (e.g. principal_id) arrive as strings.
    # Use strict=False so pydantic coerces str -> UUID.
    return Subscription(
        id=row["id"],
        filter=FilterPredicate.model_validate(filter_data, strict=False),
        enabled=row["enabled"],
        storage=row["storage"],
        principal_id=row["principal_id"],
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
