"""Dispatcher worker: durable event-processing loop.

Polls the events table via a DispatchBackend, matches events against
persistent subscriptions, executes actions through the ActionExecutor
protocol, and records per-event-action completion records for
at-least-once delivery.

The worker lives in dispatch (orchestration layer) and depends only
on protocols -- never on the concrete ServicesActionExecutor. Services
wires the concrete implementation; CLI registers the command.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from typing import TYPE_CHECKING, Any

from orxtra.dispatch._action_executor import execute_action
from orxtra.dispatch._delivery import match_subscription

if TYPE_CHECKING:
    from uuid import UUID

    import asyncpg
    from orxtra.dispatch._delivery import SourcePrincipalResolver
    from orxtra.dispatch._pg_backend import PgDispatchBackend
    from orxtra.protocols import ActionExecutor, FlushScheduler

logger = logging.getLogger(__name__)

# Default poll interval (seconds). NOTIFY is a hint; always poll as
# fallback because NOTIFY is unreliable (can be lost under load,
# during reconnects, etc.).
DEFAULT_POLL_INTERVAL: float = 5.0

# Maximum events per polling batch.
DEFAULT_BATCH_SIZE: int = 100


class DispatchWorker:
    """Durable dispatcher worker with at-least-once delivery.

    Constructor takes:
    - backend: a DispatchBackend with cursor/completion/poll methods
      (PgDispatchBackend or InMemoryDispatchBackend)
    - action_executor: the ActionExecutor protocol (for WorkflowAction)
    - flush_scheduler: the FlushScheduler protocol
    - pool: an asyncpg Pool (for LISTEN/NOTIFY)
    - cursor_name: identifies this worker's cursor position
    - events_channel: the PG NOTIFY channel name
    - poll_interval: fallback poll interval in seconds
    - batch_size: max events per poll

    The main loop:
    1. Read cursor position (or start from beginning)
    2. LISTEN on events_channel as a wake hint
    3. Poll for unprocessed events since cursor
    4. For each event: match subscriptions, check completion records,
       execute action, record completion, advance cursor
    5. Wait for NOTIFY or poll interval, repeat
    """

    def __init__(
        self,
        *,
        backend: PgDispatchBackend,
        action_executor: ActionExecutor,
        flush_scheduler: FlushScheduler,
        pool: asyncpg.Pool,
        cursor_name: str,
        events_channel: str,
        source_principal_resolver: SourcePrincipalResolver,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ) -> None:
        self._backend = backend
        self._action_executor = action_executor
        self._flush_scheduler = flush_scheduler
        self._pool = pool
        self._cursor_name = cursor_name
        self._events_channel = events_channel
        # Resolves a subscription filter's source slugs to source-principal ids.
        self._resolve_source_principals = source_principal_resolver
        self._poll_interval = poll_interval
        self._batch_size = batch_size
        self._stop_event = asyncio.Event()
        self._wake_event = asyncio.Event()
        self._listen_conn: (
            asyncpg.pool.PoolConnectionProxy[asyncpg.Record] | None
        ) = None

    async def run(self) -> None:
        """Main loop: poll, match, execute, advance, wait."""
        logger.info(
            "Dispatcher worker starting (cursor=%s, channel=%s)",
            self._cursor_name,
            self._events_channel,
        )

        # Set up LISTEN for wake hints.
        await self._setup_listen()

        try:
            while not self._stop_event.is_set():
                processed = await self._poll_and_process()
                if processed > 0:
                    # More events may be waiting; loop immediately.
                    continue
                # No events found; wait for NOTIFY or poll interval.
                self._wake_event.clear()
                with contextlib.suppress(TimeoutError):
                    await asyncio.wait_for(
                        self._wait_for_wake_or_stop(),
                        timeout=self._poll_interval,
                    )
        finally:
            await self._teardown_listen()

        logger.info("Dispatcher worker stopped (cursor=%s)", self._cursor_name)

    async def stop(self) -> None:
        """Signal the main loop to exit cleanly."""
        logger.info("Dispatcher worker stop requested (cursor=%s)", self._cursor_name)
        self._stop_event.set()
        self._wake_event.set()  # unblock any wait

    async def _wait_for_wake_or_stop(self) -> None:
        """Wait until either wake_event or stop_event is set."""
        wake_task = asyncio.create_task(self._wake_event.wait())
        stop_task = asyncio.create_task(self._stop_event.wait())
        try:
            _done, pending = await asyncio.wait(
                {wake_task, stop_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            for task in pending:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        except asyncio.CancelledError:
            wake_task.cancel()
            stop_task.cancel()
            raise

    async def _setup_listen(self) -> None:
        """Acquire a dedicated connection and LISTEN on the events channel."""
        try:
            conn = await self._pool.acquire()
            self._listen_conn = conn
            await conn.add_listener(
                self._events_channel, self._on_notify,
            )
            logger.debug(
                "LISTEN on %s established", self._events_channel,
            )
        except Exception:  # noqa: BLE001 -- LISTEN setup failure degrades to polling
            logger.warning(
                "Failed to set up LISTEN on %s; falling back to polling",
                self._events_channel,
                exc_info=True,
            )
            self._listen_conn = None

    async def _teardown_listen(self) -> None:
        """Remove the listener and release the connection."""
        if self._listen_conn is not None:
            try:
                await self._listen_conn.remove_listener(
                    self._events_channel, self._on_notify,
                )
            except Exception:  # noqa: BLE001 -- teardown must never raise
                logger.debug("Error removing listener", exc_info=True)
            try:
                await self._pool.release(self._listen_conn)
            except Exception:  # noqa: BLE001 -- teardown must never raise
                logger.debug("Error releasing listen connection", exc_info=True)
            self._listen_conn = None

    def _on_notify(
        self,
        _connection: asyncpg.Connection[Any] | asyncpg.pool.PoolConnectionProxy[Any],
        _pid: int,
        _channel: str,
        _payload: object,
    ) -> None:
        """NOTIFY callback: set the wake event to trigger an immediate poll."""
        self._wake_event.set()

    async def _poll_and_process(self) -> int:
        """Poll for new events and process them. Returns count processed."""
        cursor_pos = await self._backend.get_cursor_position(self._cursor_name)
        events = await self._backend.poll_events_since(
            cursor_pos, limit=self._batch_size,
        )

        if not events:
            return 0

        # Load subscriptions once per batch.
        subscriptions = await self._backend.list_subscriptions(enabled_only=True)

        processed = 0
        for event in events:
            if self._stop_event.is_set():
                break
            event_id: UUID = event["id"]
            event_type: str = event["event_type"]
            principal_id: UUID | None = event.get("principal_id")
            raw_data = event.get("data")
            data: dict[str, Any] | None = None
            if raw_data is not None:
                data = json.loads(raw_data) if isinstance(raw_data, str) else raw_data

            for sub in subscriptions:
                if not await match_subscription(
                    event_type, principal_id, data, sub.filter,
                    self._resolve_source_principals,
                ):
                    continue

                actions = await self._backend.list_actions(sub.id)
                for sub_action in actions:
                    # Check completion record -- skip if already done.
                    if await self._backend.is_action_completed(
                        event_id, sub_action.id,
                    ):
                        continue

                    # Execute the action.
                    result_status = "success"
                    try:
                        event_payload: dict[str, object] = {
                            "event_type": event_type,
                            "principal_id": (
                                str(principal_id) if principal_id else ""
                            ),
                            "data": data or {},
                            "event_id": str(event_id),
                        }
                        await execute_action(
                            sub_action.action,
                            [event_payload],
                            workflow_executor=self._action_executor,
                            event_fire_callback=self._make_event_fire_callback(
                                sub.principal_id,
                            ),
                        )
                    except Exception:
                        logger.exception(
                            "Action %s failed for event %s",
                            sub_action.id,
                            event_id,
                        )
                        result_status = "error"

                    # Record completion (idempotent via unique constraint).
                    await self._backend.record_completion(
                        event_id, sub_action.id, result_status,
                    )

            # Advance cursor after processing all actions for this event.
            await self._backend.advance_cursor(self._cursor_name, event_id)
            processed += 1

        return processed

    def _make_event_fire_callback(self, owner_principal_id: UUID) -> Any:
        """Create a callback for EventAction dispatch.

        A derived event re-fired by an EventAction is attributed to the OWNING
        SUBSCRIPTION's principal: the subscription whose action triggered the
        re-fire is the actor behind the new event. Every event the worker
        processes flows through a subscription, so an owner is always in scope.
        """

        async def _callback(
            event_type: str,
            data: dict[str, object] | None,
        ) -> None:
            from orxtra.trace import TraceWriter

            writer = TraceWriter(self._pool)
            await writer.write_event(
                None, event_type, data or {},
                principal_id=owner_principal_id,
            )

        return _callback
