from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Self
from uuid import UUID

import pytest
from uuid6 import uuid7

from orxtra.dispatch._pg_backend import (
    PgDispatchBackend,
    _deserialize_action,
    _serialize_action,
)
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
    ScriptAction,
    WorkflowAction,
)

if TYPE_CHECKING:
    from collections.abc import ItemsView, KeysView, ValuesView

NOW = datetime(2025, 7, 1, 12, 0, 0, tzinfo=UTC)


# -- Mock infrastructure (mirrors trace/tests/conftest.py) --


class MockRecord:
    def __init__(self, data: dict[str, object]) -> None:
        self._data = data

    def __getitem__(self, key: str) -> object:
        return self._data[key]

    def keys(self) -> KeysView[str]:
        return self._data.keys()

    def values(self) -> ValuesView[object]:
        return self._data.values()

    def items(self) -> ItemsView[str, object]:
        return self._data.items()


class MockTransaction:
    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class MockConnection:
    def __init__(self) -> None:
        self.executed: list[tuple[str, tuple[object, ...]]] = []
        self._fetch_results: list[list[dict[str, object]]] = []
        self._fetchrow_results: list[dict[str, object] | None] = []
        self._fetchval_results: list[object] = []
        self._execute_results: list[str] = []

    def queue_fetch(self, rows: list[dict[str, object]]) -> None:
        self._fetch_results.append(rows)

    def queue_fetchrow(self, row: dict[str, object] | None) -> None:
        self._fetchrow_results.append(row)

    def queue_fetchval(self, value: object) -> None:
        self._fetchval_results.append(value)

    def queue_execute(self, result: str) -> None:
        self._execute_results.append(result)

    async def execute(self, sql: str, *args: object) -> str:
        self.executed.append((sql, args))
        if self._execute_results:
            return self._execute_results.pop(0)
        return "DONE"

    async def fetch(self, sql: str, *args: object) -> list[MockRecord]:
        self.executed.append((sql, args))
        if self._fetch_results:
            rows = self._fetch_results.pop(0)
            return [MockRecord(r) for r in rows]
        return []

    async def fetchrow(self, sql: str, *args: object) -> MockRecord | None:
        self.executed.append((sql, args))
        if self._fetchrow_results:
            row = self._fetchrow_results.pop(0)
            return MockRecord(row) if row is not None else None
        return None

    async def fetchval(self, sql: str, *args: object) -> object | None:
        self.executed.append((sql, args))
        if self._fetchval_results:
            return self._fetchval_results.pop(0)
        return None

    def transaction(self) -> MockTransaction:
        return MockTransaction()


class MockPoolAcquire:
    def __init__(self, conn: MockConnection) -> None:
        self._conn = conn

    async def __aenter__(self) -> MockConnection:
        return self._conn

    async def __aexit__(self, *args: object) -> None:
        pass


class MockPool:
    def __init__(self) -> None:
        self.conn = MockConnection()

    def acquire(self) -> MockPoolAcquire:
        return MockPoolAcquire(self.conn)


@pytest.fixture
def mock_pool() -> MockPool:
    return MockPool()


@pytest.fixture
def pg_backend(mock_pool: MockPool) -> PgDispatchBackend:
    return PgDispatchBackend(mock_pool)  # type: ignore[arg-type]


# -- Helpers --


def _source(
    *,
    source_id: UUID | None = None,
    slug: str = "github",
    name: str = "GitHub Webhooks",
) -> Source:
    return Source(
        id=source_id or uuid7(),
        slug=slug,
        name=name,
        created_at=NOW,
    )


def _sub(
    *,
    sub_id: UUID | None = None,
    enabled: bool = True,
    event_types: list[str] | None = None,
) -> Subscription:
    return Subscription(
        id=sub_id or uuid7(),
        filter=FilterPredicate(event_types=event_types),
        enabled=enabled,
        created_at=NOW,
    )


def _action(
    *,
    action_id: UUID | None = None,
    sub_id: UUID,
    position: int = 0,
    action: ScriptAction | LogAction | WorkflowAction | EventAction | None = None,
) -> SubscriptionAction:
    return SubscriptionAction(
        id=action_id or uuid7(),
        subscription_id=sub_id,
        position=position,
        action=action or ScriptAction(callable="my.module:handler"),
        created_at=NOW,
    )


def _accum(
    *,
    entry_id: UUID | None = None,
    action_id: UUID,
    event_id: UUID | None = None,
) -> AccumulatorEntry:
    return AccumulatorEntry(
        id=entry_id or uuid7(),
        subscription_action_id=action_id,
        event_id=event_id or uuid7(),
        created_at=NOW,
    )


# -- Action serialization/deserialization --


class TestActionSerialization:
    def test_serialize_script_action(self) -> None:
        action = ScriptAction(callable="my.module:handler")
        action_type, config_json = _serialize_action(action)
        assert action_type == "script"
        data = json.loads(config_json)
        assert data == {"callable": "my.module:handler"}

    def test_serialize_log_action(self) -> None:
        action = LogAction(message="hello", level="warn")
        action_type, config_json = _serialize_action(action)
        assert action_type == "log"
        data = json.loads(config_json)
        assert data == {"message": "hello", "level": "warn"}

    def test_serialize_workflow_action(self) -> None:
        action = WorkflowAction(workflow_path="/path/to/wf", config={"key": "val"})
        action_type, config_json = _serialize_action(action)
        assert action_type == "workflow"
        data = json.loads(config_json)
        assert data == {"workflow_path": "/path/to/wf", "config": {"key": "val"}}

    def test_serialize_event_action(self) -> None:
        action = EventAction(event_type="task.done", data={"id": 1})
        action_type, config_json = _serialize_action(action)
        assert action_type == "event"
        data = json.loads(config_json)
        assert data["event_type"] == "task.done"
        assert data["data"] == {"id": 1}

    def test_deserialize_script_action(self) -> None:
        result = _deserialize_action("script", '{"callable": "x:y"}')
        assert isinstance(result, ScriptAction)
        assert result.callable == "x:y"

    def test_deserialize_log_action(self) -> None:
        result = _deserialize_action("log", '{"message": "hi", "level": "info"}')
        assert isinstance(result, LogAction)
        assert result.message == "hi"

    def test_deserialize_workflow_action(self) -> None:
        result = _deserialize_action(
            "workflow", '{"workflow_path": "/wf", "config": {}}',
        )
        assert isinstance(result, WorkflowAction)
        assert result.workflow_path == "/wf"

    def test_deserialize_event_action(self) -> None:
        result = _deserialize_action(
            "event",
            '{"event_type": "done", "data": {}, "source": "internal"}',
        )
        assert isinstance(result, EventAction)
        assert result.event_type == "done"

    def test_deserialize_unknown_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown action_type"):
            _deserialize_action("bogus", "{}")

    def test_serialize_unknown_type_raises(self) -> None:
        # Pass something that isn't in the action type map.
        with pytest.raises(TypeError, match="Unknown action type"):
            _serialize_action(object())  # type: ignore[arg-type]

    def test_roundtrip_all_action_types(self) -> None:
        actions = [
            ScriptAction(callable="a:b"),
            LogAction(message="msg"),
            WorkflowAction(workflow_path="/w"),
            EventAction(event_type="e"),
        ]
        for original in actions:
            atype, config = _serialize_action(original)
            restored = _deserialize_action(atype, config)
            assert restored == original


# -- SourceStorage --


class TestSourceStorage:
    async def test_create_source_sql(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        src = _source()
        result = await pg_backend.create_source(src)
        assert result == src.id

        sql, args = mock_pool.conn.executed[0]
        assert "INSERT INTO sources" in sql
        assert "$1" in sql
        assert args[0] == src.id
        assert args[1] == "github"
        assert args[2] == "GitHub Webhooks"
        # auth_method and auth_config are None
        assert args[3] is None
        assert args[4] is None

    async def test_create_source_with_auth(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        src = Source(
            id=uuid7(),
            slug="stripe",
            name="Stripe",
            auth_method="hmac",
            auth_config={"secret": "whsec_xxx"},
            created_at=NOW,
        )
        await pg_backend.create_source(src)

        _, args = mock_pool.conn.executed[0]
        assert args[3] == "hmac"
        assert json.loads(args[4]) == {"secret": "whsec_xxx"}

    async def test_get_source_found(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        source_id = uuid7()
        mock_pool.conn.queue_fetchrow({
            "id": source_id,
            "slug": "gh",
            "name": "GitHub",
            "auth_method": None,
            "auth_config": None,
            "created_at": NOW,
        })
        result = await pg_backend.get_source(source_id)
        assert result is not None
        assert result.id == source_id
        assert result.slug == "gh"

        sql, args = mock_pool.conn.executed[0]
        assert "FROM sources WHERE id = $1" in sql
        assert args[0] == source_id

    async def test_get_source_not_found(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow(None)
        result = await pg_backend.get_source(uuid7())
        assert result is None

    async def test_get_source_by_slug(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        source_id = uuid7()
        mock_pool.conn.queue_fetchrow({
            "id": source_id,
            "slug": "stripe",
            "name": "Stripe",
            "auth_method": None,
            "auth_config": None,
            "created_at": NOW,
        })
        result = await pg_backend.get_source_by_slug("stripe")
        assert result is not None
        assert result.slug == "stripe"

        sql, args = mock_pool.conn.executed[0]
        assert "WHERE slug = $1" in sql
        assert args[0] == "stripe"

    async def test_list_sources(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetch([
            {
                "id": uuid7(), "slug": "a", "name": "A",
                "auth_method": None, "auth_config": None,
                "created_at": NOW,
            },
            {
                "id": uuid7(), "slug": "b", "name": "B",
                "auth_method": None, "auth_config": None,
                "created_at": NOW,
            },
        ])
        result = await pg_backend.list_sources()
        assert len(result) == 2

        sql, _ = mock_pool.conn.executed[0]
        assert "FROM sources ORDER BY created_at" in sql

    async def test_delete_source(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        source_id = uuid7()
        await pg_backend.delete_source(source_id)

        sql, args = mock_pool.conn.executed[0]
        assert "DELETE FROM sources WHERE id = $1" in sql
        assert args[0] == source_id


# -- SubscriptionStorage --


class TestSubscriptionStorage:
    async def test_create_subscription_sql(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub = _sub(event_types=["task.done", "task.failed"])
        result = await pg_backend.create_subscription(sub)
        assert result == sub.id

        sql, args = mock_pool.conn.executed[0]
        assert "INSERT INTO subscriptions" in sql
        assert args[0] == sub.id
        # filter_expr is JSON
        filter_data = json.loads(args[1])
        assert filter_data["event_types"] == ["task.done", "task.failed"]
        assert args[2] is True  # enabled
        assert args[3] == "persistent"  # storage
        assert args[4] is None  # owner_run_id

    async def test_get_subscription_found(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        mock_pool.conn.queue_fetchrow({
            "id": sub_id,
            "filter_expr": json.dumps({
                "event_types": ["x"],
                "sources": None,
                "data_predicates": None,
            }),
            "enabled": True,
            "storage": "persistent",
            "owner_run_id": None,
            "created_at": NOW,
        })
        result = await pg_backend.get_subscription(sub_id)
        assert result is not None
        assert result.id == sub_id
        assert result.filter.event_types == ["x"]

    async def test_get_subscription_not_found(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetchrow(None)
        result = await pg_backend.get_subscription(uuid7())
        assert result is None

    async def test_list_subscriptions_enabled_only(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetch([])
        await pg_backend.list_subscriptions(enabled_only=True)

        sql, _ = mock_pool.conn.executed[0]
        assert "WHERE enabled = true" in sql

    async def test_list_subscriptions_all(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetch([])
        await pg_backend.list_subscriptions(enabled_only=False)

        sql, _ = mock_pool.conn.executed[0]
        assert "WHERE enabled" not in sql

    async def test_update_subscription_success(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        mock_pool.conn.queue_execute("UPDATE 1")
        await pg_backend.update_subscription(sub_id, enabled=False)

        sql, args = mock_pool.conn.executed[0]
        assert "UPDATE subscriptions SET enabled = $1" in sql
        assert args[0] is False
        assert args[1] == sub_id

    async def test_update_subscription_not_found_raises(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_execute("UPDATE 0")
        with pytest.raises(KeyError, match="not found"):
            await pg_backend.update_subscription(uuid7(), enabled=False)

    async def test_delete_subscription(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        await pg_backend.delete_subscription(sub_id)

        sql, args = mock_pool.conn.executed[0]
        assert "DELETE FROM subscriptions WHERE id = $1" in sql
        assert args[0] == sub_id


# -- ActionStorage --


class TestActionStorage:
    async def test_create_action_sql(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        act = _action(sub_id=sub_id, position=2)
        result = await pg_backend.create_action(act)
        assert result == act.id

        sql, args = mock_pool.conn.executed[0]
        assert "INSERT INTO subscription_actions" in sql
        assert args[0] == act.id
        assert args[1] == sub_id
        assert args[2] == 2  # position
        assert args[3] == "script"  # action_type
        config = json.loads(args[4])
        assert config == {"callable": "my.module:handler"}
        assert args[5] is None  # accumulator_config

    async def test_create_action_with_accumulator_config(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        act = SubscriptionAction(
            id=uuid7(),
            subscription_id=sub_id,
            position=0,
            action=LogAction(message="test"),
            accumulator_config={"batch_size": 10, "window_seconds": 60},
            created_at=NOW,
        )
        await pg_backend.create_action(act)

        _, args = mock_pool.conn.executed[0]
        assert args[3] == "log"
        accum = json.loads(args[5])
        assert accum == {"batch_size": 10, "window_seconds": 60}

    async def test_list_actions(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        action_id = uuid7()
        mock_pool.conn.queue_fetch([{
            "id": action_id,
            "subscription_id": sub_id,
            "position": 0,
            "action_type": "script",
            "action_config": json.dumps({"callable": "x:y"}),
            "accumulator_config": None,
            "created_at": NOW,
        }])
        result = await pg_backend.list_actions(sub_id)
        assert len(result) == 1
        assert result[0].id == action_id
        assert isinstance(result[0].action, ScriptAction)
        assert result[0].action.callable == "x:y"

        sql, args = mock_pool.conn.executed[0]
        assert "WHERE subscription_id = $1" in sql
        assert "ORDER BY position" in sql
        assert args[0] == sub_id

    async def test_list_actions_deserializes_all_types(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        mock_pool.conn.queue_fetch([
            {
                "id": uuid7(), "subscription_id": sub_id, "position": 0,
                "action_type": "log",
                "action_config": json.dumps({"message": "hi", "level": "info"}),
                "accumulator_config": None, "created_at": NOW,
            },
            {
                "id": uuid7(), "subscription_id": sub_id, "position": 1,
                "action_type": "workflow",
                "action_config": json.dumps(
                    {"workflow_path": "/w", "config": {}},
                ),
                "accumulator_config": None, "created_at": NOW,
            },
            {
                "id": uuid7(), "subscription_id": sub_id, "position": 2,
                "action_type": "event",
                "action_config": json.dumps(
                    {"event_type": "e", "data": {}, "source": "internal"},
                ),
                "accumulator_config": None, "created_at": NOW,
            },
        ])
        result = await pg_backend.list_actions(sub_id)
        assert isinstance(result[0].action, LogAction)
        assert isinstance(result[1].action, WorkflowAction)
        assert isinstance(result[2].action, EventAction)

    async def test_delete_actions(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        await pg_backend.delete_actions(sub_id)

        sql, args = mock_pool.conn.executed[0]
        assert "DELETE FROM subscription_actions" in sql
        assert "WHERE subscription_id = $1" in sql
        assert args[0] == sub_id


# -- AccumulatorStorage --


class TestAccumulatorStorage:
    async def test_buffer_event_sql(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        entry = _accum(action_id=uuid7())
        result = await pg_backend.buffer_event(entry)
        assert result == entry.id

        sql, args = mock_pool.conn.executed[0]
        assert "INSERT INTO accumulator_buffer" in sql
        assert args[0] == entry.id
        assert args[1] == entry.subscription_action_id
        assert args[2] == entry.event_id

    async def test_claim_batch_uses_for_update_skip_locked(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        action_id = uuid7()
        entry_id = uuid7()
        mock_pool.conn.queue_fetch([{
            "id": entry_id,
            "subscription_action_id": action_id,
            "event_id": uuid7(),
            "created_at": NOW,
        }])
        result = await pg_backend.claim_batch(action_id, limit=50)
        assert len(result) == 1
        assert result[0].id == entry_id

        # First call: SELECT ... FOR UPDATE SKIP LOCKED
        sql, args = mock_pool.conn.executed[0]
        assert "FOR UPDATE SKIP LOCKED" in sql
        assert "ORDER BY created_at" in sql
        assert "LIMIT $2" in sql
        assert args[0] == action_id
        assert args[1] == 50

        # Second call: DELETE claimed rows
        sql2, args2 = mock_pool.conn.executed[1]
        assert "DELETE FROM accumulator_buffer" in sql2
        assert "ANY($1::uuid[])" in sql2
        assert args2[0] == [entry_id]

    async def test_claim_batch_empty_no_delete(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        mock_pool.conn.queue_fetch([])
        result = await pg_backend.claim_batch(uuid7())
        assert result == []
        # Only the SELECT, no DELETE.
        assert len(mock_pool.conn.executed) == 1

    async def test_confirm_batch_deletes(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        ids = [uuid7(), uuid7()]
        await pg_backend.confirm_batch(ids)

        sql, args = mock_pool.conn.executed[0]
        assert "DELETE FROM accumulator_buffer" in sql
        assert "ANY($1::uuid[])" in sql
        assert args[0] == ids

    async def test_confirm_batch_empty_is_noop(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        await pg_backend.confirm_batch([])
        assert len(mock_pool.conn.executed) == 0

    async def test_pending_count(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        action_id = uuid7()
        mock_pool.conn.queue_fetchval(42)
        result = await pg_backend.pending_count(action_id)
        assert result == 42

        sql, args = mock_pool.conn.executed[0]
        assert "count(*)" in sql
        assert "WHERE subscription_action_id = $1" in sql
        assert args[0] == action_id


# -- FilterPredicate serialization --


class TestFilterPredicateSerialization:
    async def test_filter_with_all_fields(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub = Subscription(
            id=uuid7(),
            filter=FilterPredicate(
                event_types=["a", "b"],
                sources=["github"],
                data_predicates={"key": "value"},
            ),
            enabled=True,
            created_at=NOW,
        )
        await pg_backend.create_subscription(sub)

        _, args = mock_pool.conn.executed[0]
        filter_data = json.loads(args[1])
        assert filter_data["event_types"] == ["a", "b"]
        assert filter_data["sources"] == ["github"]
        assert filter_data["data_predicates"] == {"key": "value"}

    async def test_filter_roundtrip_through_get(
        self, pg_backend: PgDispatchBackend, mock_pool: MockPool,
    ) -> None:
        sub_id = uuid7()
        original_filter = {
            "event_types": ["task.done"],
            "sources": None,
            "data_predicates": None,
        }
        mock_pool.conn.queue_fetchrow({
            "id": sub_id,
            "filter_expr": json.dumps(original_filter),
            "enabled": True,
            "storage": "transient",
            "owner_run_id": None,
            "created_at": NOW,
        })
        result = await pg_backend.get_subscription(sub_id)
        assert result is not None
        assert result.filter.event_types == ["task.done"]
        assert result.filter.sources is None
        assert result.storage == "transient"
