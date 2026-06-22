"""Phase 4 tests: cross-process PG LISTEN signal delivery."""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import uuid6
from orxtra.protocols._task import TaskSpec
from orxtra.scheduler._types import WorkflowConfig

if TYPE_CHECKING:
    import uuid
    from collections.abc import Callable, Iterator

    from orxtra.scheduler._executor import Scheduler

    from tests.conftest import MockTraceWriter

type _SchedulerFactory = Callable[..., Scheduler]
type _ListenerCallback = Callable[[object, int, str, str], None]


@contextmanager
def _patch_recovery() -> Iterator[None]:
    """Patch crash recovery functions so pool != None
    tests skip real PG calls."""
    with (
        patch(
            "orxtra.trace.reclaim_interrupted",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "orxtra.trace.reevaluate_blocked",
            new_callable=AsyncMock,
            return_value=[],
        ),
        patch(
            "orxtra.trace.clean_orphaned",
            new_callable=AsyncMock,
            return_value=0,
        ),
        patch(
            "orxtra.trace.acquire_run_lock",
            new_callable=AsyncMock,
        ),
    ):
        yield


def _decision_point_task(name: str = "dp1") -> TaskSpec:
    return TaskSpec(name=name, decision_point=True)


def _decision_point_workflow(
    tasks: list[TaskSpec] | None = None,
) -> WorkflowConfig:
    if tasks is None:
        tasks = [_decision_point_task()]
    return WorkflowConfig(
        name="test-workflow",
        description="PG listen test",
        tasks=tasks,
        dependencies={},
    )


class TestPgListener:
    """Tests for cross-process PG LISTEN delivery (Phase 4)."""

    async def test_pg_listener_dispatches_control_signal(
        self,
        make_scheduler: _SchedulerFactory,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """PG notification with run_state event triggers
        _handle_control_signal."""
        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        scheduler = make_scheduler(pool=mock_pool)

        # We need to capture the listener callback that gets
        # registered via add_listener
        captured_callback = None

        async def capture_add_listener(
            channel: str, callback: _ListenerCallback,
        ) -> None:
            nonlocal captured_callback
            captured_callback = callback

        mock_conn.add_listener = capture_add_listener
        mock_conn.remove_listener = AsyncMock()

        config = _decision_point_workflow()
        # Run execute_workflow — it should set up the PG listener
        with _patch_recovery():
            await scheduler.execute_workflow(config)

        # The listener should have been registered
        assert captured_callback is not None

        # Now simulate a PG notification for run_state
        payload = json.dumps({
            "event_type": "run_state",
            "run_id": str(run_id),
            "new_status": "paused",
        })
        captured_callback(mock_conn, 0, "orxtra_events", payload)

        # Give the scheduled task time to execute
        await asyncio.sleep(0.05)

        assert scheduler.is_paused

    async def test_pg_listener_dispatches_to_event_registry(
        self,
        make_scheduler: _SchedulerFactory,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """PG notification with non-run_state event fires on
        EventRegistry."""
        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        scheduler = make_scheduler(pool=mock_pool)

        captured_callback = None

        async def capture_add_listener(
            channel: str, callback: _ListenerCallback,
        ) -> None:
            nonlocal captured_callback
            captured_callback = callback

        mock_conn.add_listener = capture_add_listener
        mock_conn.remove_listener = AsyncMock()

        config = _decision_point_workflow()
        with _patch_recovery():
            await scheduler.execute_workflow(config)
        assert captured_callback is not None

        # Set up a waiter on the event registry
        received: dict[str, object] | None = None

        async def wait_for_event() -> None:
            nonlocal received
            received = await scheduler._event_registry.wait_for(  # noqa: SLF001
                "custom_event", deadline_seconds=2.0,
            )

        waiter = asyncio.create_task(wait_for_event())
        await asyncio.sleep(0.01)

        # Fire a non-run_state notification
        payload = json.dumps({
            "event_type": "custom_event",
            "run_id": str(uuid6.uuid7()),  # different run
            "data": {"key": "value"},
        })
        captured_callback(mock_conn, 0, "orxtra_events", payload)

        await asyncio.sleep(0.05)
        await waiter
        assert received == {"key": "value"}

    async def test_no_pool_no_listener(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
    ) -> None:
        """When pool is None, no PG listener is started.
        execute_workflow completes normally."""
        assert scheduler._pool is None  # noqa: SLF001
        config = _decision_point_workflow()
        # Should complete without errors
        await scheduler.execute_workflow(config)

        # Verify no PG-related calls happened — only standard
        # subscribe/unsubscribe calls
        subs = trace_writer.get_calls("subscribe_run_control")
        unsubs = trace_writer.get_calls("unsubscribe_run_control")
        assert len(subs) == 1
        assert len(unsubs) == 1

    async def test_listener_cleaned_up_on_workflow_exit(
        self,
        make_scheduler: _SchedulerFactory,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """PG listener connection is released and listener removed
        when workflow completes."""
        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        scheduler = make_scheduler(pool=mock_pool)

        captured_callback = None

        async def capture_add_listener(
            channel: str, callback: _ListenerCallback,
        ) -> None:
            nonlocal captured_callback
            captured_callback = callback

        mock_conn.add_listener = capture_add_listener
        mock_conn.remove_listener = AsyncMock()

        config = _decision_point_workflow()
        with _patch_recovery():
            await scheduler.execute_workflow(config)

        # Verify cleanup happened
        mock_conn.remove_listener.assert_awaited_once()
        remove_args = mock_conn.remove_listener.call_args
        assert remove_args[0][0] == "orxtra_events"
        mock_pool.release.assert_awaited_once_with(mock_conn)

    async def test_invalid_json_payload_ignored(
        self,
        make_scheduler: _SchedulerFactory,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Invalid JSON in PG notification is logged and ignored."""
        mock_conn = AsyncMock()
        mock_pool = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        scheduler = make_scheduler(pool=mock_pool)

        captured_callback = None

        async def capture_add_listener(
            channel: str, callback: _ListenerCallback,
        ) -> None:
            nonlocal captured_callback
            captured_callback = callback

        mock_conn.add_listener = capture_add_listener
        mock_conn.remove_listener = AsyncMock()

        config = _decision_point_workflow()
        with _patch_recovery():
            await scheduler.execute_workflow(config)
        assert captured_callback is not None

        # Send invalid JSON — should not raise
        captured_callback(
            mock_conn, 0, "orxtra_events", "not-json!!!",
        )
        await asyncio.sleep(0.01)

        # Scheduler should still be in a valid state
        assert not scheduler.is_paused
