from __future__ import annotations

import asyncio
import json
import threading
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import asyncpg  # type: ignore[import-untyped]
import pytest
from orxtra.services._events import event_stream, fire_event
from orxtra.trace import EVENTS_CHANNEL, InMemoryEventBus

if TYPE_CHECKING:
    from uuid import UUID


@pytest.fixture
def mock_pool() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_writer() -> AsyncMock:
    writer = AsyncMock()
    writer.write_event = AsyncMock(return_value=(uuid4(), True))
    return writer


# ── fire_event tests ──


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_basic(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    expected_id = uuid4()
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(expected_id, True))

    event_id, inserted = await fire_event(mock_pool, sample_run_id, "task_started")

    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_started", {},
        source="internal", idempotency_key=None,
    )
    assert event_id == expected_id
    assert inserted is True


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_with_payload(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    expected_id = uuid4()
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(expected_id, True))
    payload = {"task_id": "abc", "status": "done"}

    event_id, inserted = await fire_event(
        mock_pool, sample_run_id, "task_completed", payload=payload
    )

    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_completed", payload,
        source="internal", idempotency_key=None,
    )
    assert event_id == expected_id
    assert inserted is True


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_none_payload(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(uuid4(), True))

    await fire_event(mock_pool, sample_run_id, "ping", payload=None)

    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "ping", {},
        source="internal", idempotency_key=None,
    )


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_propagates_write_error(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(
        side_effect=asyncpg.ForeignKeyViolationError(
            'insert or update on table "events" violates'
            " foreign key constraint"
        )
    )

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await fire_event(mock_pool, sample_run_id, "task_started")


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_with_source(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    expected_id = uuid4()
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(expected_id, True))

    event_id, inserted = await fire_event(
        mock_pool, sample_run_id, "task_started", source="agent"
    )

    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "task_started", {},
        source="agent", idempotency_key=None,
    )
    assert event_id == expected_id
    assert inserted is True


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_with_idempotency_key(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    """fire_event passes idempotency_key through to TraceWriter."""
    expected_id = uuid4()
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(expected_id, True))

    event_id, inserted = await fire_event(
        mock_pool, sample_run_id, "webhook_event",
        idempotency_key="github-delivery-abc123",
    )

    mock_writer.write_event.assert_awaited_once_with(
        sample_run_id, "webhook_event", {},
        source="internal", idempotency_key="github-delivery-abc123",
    )
    assert event_id == expected_id
    assert inserted is True


@pytest.mark.asyncio
@patch("orxtra.services._events.TraceWriter")
async def test_fire_event_idempotency_dedup(
    mock_writer_cls: AsyncMock, mock_pool: AsyncMock, sample_run_id: UUID
) -> None:
    """When idempotency_key triggers dedup, inserted is False."""
    expected_id = uuid4()
    mock_writer = mock_writer_cls.return_value
    mock_writer.write_event = AsyncMock(return_value=(expected_id, False))

    event_id, inserted = await fire_event(
        mock_pool, sample_run_id, "webhook_event",
        idempotency_key="github-delivery-dup",
    )

    assert event_id == expected_id
    assert inserted is False


# ── fire_blocking tests ──


def test_fire_blocking_calls_fire_event(sample_run_id: UUID) -> None:
    """fire_blocking wraps fire_event synchronously via asyncio.run."""
    expected_id = uuid4()

    with patch("orxtra.services._events.TraceWriter") as mock_writer_cls:
        mock_writer = mock_writer_cls.return_value
        mock_writer.write_event = AsyncMock(return_value=(expected_id, True))

        from orxtra.services._events import fire_blocking

        event_id, inserted = fire_blocking(AsyncMock(), sample_run_id, "test_event")

    assert event_id == expected_id
    assert inserted is True


def test_fire_blocking_with_source(sample_run_id: UUID) -> None:
    """fire_blocking passes source parameter through to fire_event."""
    expected_id = uuid4()

    with patch("orxtra.services._events.TraceWriter") as mock_writer_cls:
        mock_writer = mock_writer_cls.return_value
        mock_writer.write_event = AsyncMock(return_value=(expected_id, True))

        from orxtra.services._events import fire_blocking

        event_id, inserted = fire_blocking(
            AsyncMock(), sample_run_id, "test_event", source="agent"
        )

    assert event_id == expected_id
    assert inserted is True


def test_fire_blocking_from_different_thread(sample_run_id: UUID) -> None:
    """fire_blocking works when called from a thread with a running loop elsewhere."""
    expected_id = uuid4()
    loop = asyncio.new_event_loop()
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()

    try:
        with patch("orxtra.services._events.TraceWriter") as mock_writer_cls:
            mock_writer = mock_writer_cls.return_value
            mock_writer.write_event = AsyncMock(return_value=(expected_id, True))

            from orxtra.services._events import fire_blocking

            event_id, inserted = fire_blocking(AsyncMock(), sample_run_id, "test_event")

        assert event_id == expected_id
        assert inserted is True
    finally:
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        loop.close()


def test_fire_blocking_from_loop_thread_raises(sample_run_id: UUID) -> None:
    """fire_blocking raises RuntimeError when called from the event loop thread."""

    async def _inner() -> None:
        with patch("orxtra.services._events.TraceWriter") as mock_writer_cls:
            mock_writer = mock_writer_cls.return_value
            mock_writer.write_event = AsyncMock(return_value=(uuid4(), True))

            from orxtra.services._events import fire_blocking

            fire_blocking(AsyncMock(), sample_run_id, "test_event")

    with pytest.raises(RuntimeError, match="run_sync\\(\\) called from the event loop thread"):
        asyncio.run(_inner())


# ── event_stream tests ──


class TestEventStream:
    """Tests for event_stream async generator.

    The async generator subscribes to the EventBus on its first __anext__ call
    (the code before the first yield). Tests use asyncio.create_task to drive
    iteration, which triggers the subscription; then events are published and
    the generator yields them.
    """

    @pytest.mark.asyncio
    async def test_basic_event_stream(self) -> None:
        """event_stream yields parsed events from an EventBus."""
        bus = InMemoryEventBus()
        payload = json.dumps({
            "event_type": "task_started",
            "data": {"task": "abc"},
        })

        received: list[dict[str, object]] = []

        async def _collect() -> None:
            async for ev in event_stream(bus):
                received.append(ev)
                break

        task = asyncio.create_task(_collect())
        # Let the task run to the first __anext__, which subscribes
        await asyncio.sleep(0)

        await bus.publish(EVENTS_CHANNEL, payload)
        await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0]["event_type"] == "task_started"

    @pytest.mark.asyncio
    async def test_event_stream_filters_by_run_id(self) -> None:
        """event_stream filters events by run_id when specified."""
        bus = InMemoryEventBus()
        target_run_id = uuid4()
        other_run_id = uuid4()

        received: list[dict[str, object]] = []

        async def _collect() -> None:
            async for ev in event_stream(bus, run_id=target_run_id):
                received.append(ev)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        # Wrong run_id -- filtered out
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "run_id": str(other_run_id),
            "event_type": "task_started",
        }))
        await asyncio.sleep(0)

        # Correct run_id -- passes through
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "run_id": str(target_run_id),
            "event_type": "task_completed",
        }))
        await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0]["event_type"] == "task_completed"

    @pytest.mark.asyncio
    async def test_event_stream_filters_by_event_types(self) -> None:
        """event_stream filters events by event_types when specified."""
        bus = InMemoryEventBus()

        received: list[dict[str, object]] = []

        async def _collect() -> None:
            async for ev in event_stream(bus, event_types=["task_completed"]):
                received.append(ev)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        # Non-matching type
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "event_type": "task_started",
        }))
        await asyncio.sleep(0)

        # Matching type
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "event_type": "task_completed",
            "data": {"result": "ok"},
        }))
        await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0]["event_type"] == "task_completed"

    @pytest.mark.asyncio
    async def test_event_stream_custom_channel(self) -> None:
        """event_stream subscribes to the specified channel."""
        bus = InMemoryEventBus()

        received: list[dict[str, object]] = []

        async def _collect() -> None:
            async for ev in event_stream(bus, channel="custom_channel"):
                received.append(ev)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        # Default channel -- not received
        await bus.publish(EVENTS_CHANNEL, json.dumps({"event_type": "nope"}))
        await asyncio.sleep(0)

        # Custom channel -- received
        await bus.publish("custom_channel", json.dumps({
            "event_type": "custom_event",
        }))
        await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0]["event_type"] == "custom_event"

    @pytest.mark.asyncio
    async def test_event_stream_multiple_events(self) -> None:
        """event_stream yields multiple events in order."""
        bus = InMemoryEventBus()

        received: list[dict[str, object]] = []

        async def _collect() -> None:
            async for ev in event_stream(bus):
                received.append(ev)
                if len(received) >= 3:
                    break

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        for i in range(3):
            await bus.publish(EVENTS_CHANNEL, json.dumps({
                "event_type": "ev",
                "index": i,
            }))
            await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 3
        assert [r["index"] for r in received] == [0, 1, 2]

    @pytest.mark.asyncio
    async def test_event_stream_combined_filters(self) -> None:
        """event_stream applies both run_id and event_types filters."""
        bus = InMemoryEventBus()
        target_run = uuid4()
        other_run = uuid4()

        received: list[dict[str, object]] = []

        async def _collect() -> None:
            async for ev in event_stream(
                bus,
                run_id=target_run,
                event_types=["task_completed"],
            ):
                received.append(ev)
                if len(received) >= 1:
                    break

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        # Wrong run_id, right type
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "run_id": str(other_run),
            "event_type": "task_completed",
        }))
        await asyncio.sleep(0)

        # Right run_id, wrong type
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "run_id": str(target_run),
            "event_type": "task_started",
        }))
        await asyncio.sleep(0)

        # Right run_id, right type
        await bus.publish(EVENTS_CHANNEL, json.dumps({
            "run_id": str(target_run),
            "event_type": "task_completed",
            "data": {"match": True},
        }))
        await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        assert len(received) == 1
        assert received[0]["data"] == {"match": True}


class TestEventStreamCleanup:
    """event_stream unsubscribes from the bus when the consumer stops."""

    @pytest.mark.asyncio
    async def test_cleanup_on_break(self) -> None:
        """Breaking out of event_stream and closing triggers unsubscribe."""
        bus = InMemoryEventBus()

        async def _collect() -> None:
            gen = event_stream(bus)
            async for _ev in gen:
                break
            # Explicitly close to trigger finally.
            await gen.aclose()

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        # Publish one event so the break happens.
        await bus.publish(EVENTS_CHANNEL, json.dumps({"event_type": "x"}))
        await asyncio.sleep(0)

        await asyncio.wait_for(task, timeout=1.0)

        # After the generator finishes, the bus should have no subscribers.
        subs = bus._subscribers.get(EVENTS_CHANNEL, [])
        assert len(subs) == 0

    @pytest.mark.asyncio
    async def test_cleanup_on_cancellation(self) -> None:
        """Cancelling the consumer task triggers unsubscribe via finally."""
        bus = InMemoryEventBus()

        async def _collect() -> None:
            gen = event_stream(bus)
            try:
                async for _ev in gen:
                    pass
            except asyncio.CancelledError:
                # Explicitly close to trigger finally.
                await gen.aclose()
                raise

        task = asyncio.create_task(_collect())
        await asyncio.sleep(0)

        # Verify subscription exists.
        assert EVENTS_CHANNEL in bus._subscribers
        assert len(bus._subscribers[EVENTS_CHANNEL]) == 1

        # Cancel.
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass

        subs = bus._subscribers.get(EVENTS_CHANNEL, [])
        assert len(subs) == 0
