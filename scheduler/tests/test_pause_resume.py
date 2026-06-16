from __future__ import annotations

import asyncio
import contextlib
from typing import TYPE_CHECKING

import pytest
import uuid6
from orxt.protocols._task import TaskSpec, TaskState

if TYPE_CHECKING:
    import uuid

    from orxt.scheduler._executor import Scheduler

    from tests.conftest import MockTraceWriter


@pytest.mark.asyncio
async def test_pause_sets_flag(
    scheduler: Scheduler,
) -> None:
    assert not scheduler.is_paused
    await scheduler.pause()
    assert scheduler.is_paused


@pytest.mark.asyncio
async def test_resume_clears_flag(
    scheduler: Scheduler,
) -> None:
    await scheduler.pause()
    assert scheduler.is_paused
    await scheduler.resume()
    assert not scheduler.is_paused


@pytest.mark.asyncio
async def test_pause_transitions_run(
    scheduler: Scheduler,
    trace_writer: MockTraceWriter,
    run_id: uuid.UUID,
) -> None:
    await scheduler.pause()
    calls = trace_writer.get_calls("transition_run")
    assert len(calls) == 1
    assert calls[0]["run_id"] == run_id
    assert calls[0]["new_status"] == "paused"


@pytest.mark.asyncio
async def test_resume_transitions_run(
    scheduler: Scheduler,
    trace_writer: MockTraceWriter,
    run_id: uuid.UUID,
) -> None:
    await scheduler.pause()
    await scheduler.resume()
    calls = trace_writer.get_calls("transition_run")
    assert len(calls) == 2
    assert calls[1]["run_id"] == run_id
    assert calls[1]["new_status"] == "running"


@pytest.mark.asyncio
async def test_paused_scheduler_blocks_task(
    scheduler: Scheduler,
) -> None:
    await scheduler.pause()
    task = TaskSpec(
        name="blocked",
        agent="test-agent",
        task_prompt="Do it",
    )
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(
            scheduler.execute_task(task, None),
            timeout=0.1,
        )


@pytest.mark.asyncio
async def test_resume_after_pause_continues(
    scheduler: Scheduler,
) -> None:
    await scheduler.pause()
    assert scheduler.is_paused

    async def delayed_resume() -> None:
        await asyncio.sleep(0.05)
        await scheduler.resume()

    asyncio.create_task(delayed_resume())

    task = TaskSpec(
        name="continued",
        agent="test-agent",
        task_prompt="Do it",
    )
    result = await asyncio.wait_for(
        scheduler.execute_task(task, None),
        timeout=2.0,
    )
    assert result is not None


@pytest.mark.asyncio
async def test_pause_cancels_running_tasks(
    scheduler: Scheduler,
) -> None:
    async def long_running() -> None:
        await asyncio.sleep(100)

    fake_task = asyncio.create_task(long_running())
    scheduler._running_tasks.add(fake_task)

    await scheduler.pause()
    # Let the event loop process the cancellation
    with contextlib.suppress(asyncio.CancelledError):
        await fake_task
    assert fake_task.cancelled()


@pytest.mark.asyncio
async def test_pause_does_not_change_task_states(
    scheduler: Scheduler,
) -> None:
    tid = uuid6.uuid7()
    scheduler._task_states[tid] = TaskState.ACTIVE
    await scheduler.pause()
    assert scheduler._task_states[tid] == TaskState.ACTIVE


@pytest.mark.asyncio
async def test_pause_when_already_paused(
    scheduler: Scheduler,
    trace_writer: MockTraceWriter,
) -> None:
    await scheduler.pause()
    assert scheduler.is_paused
    # Calling pause again should not error
    await scheduler.pause()
    assert scheduler.is_paused
    calls = trace_writer.get_calls("transition_run")
    assert len(calls) == 2
    assert all(
        c["new_status"] == "paused" for c in calls
    )


@pytest.mark.asyncio
async def test_resume_when_not_paused(
    scheduler: Scheduler,
    trace_writer: MockTraceWriter,
) -> None:
    assert not scheduler.is_paused
    # Calling resume when not paused should not error
    await scheduler.resume()
    assert not scheduler.is_paused
    calls = trace_writer.get_calls("transition_run")
    assert len(calls) == 1
    assert calls[0]["new_status"] == "running"
