from __future__ import annotations

import asyncio

import pytest

from orxtra.protocols import FlushScheduler
from orxtra.services._flush import AsyncioFlushScheduler


@pytest.fixture
def scheduler() -> AsyncioFlushScheduler:
    return AsyncioFlushScheduler()


async def test_satisfies_protocol() -> None:
    instance = AsyncioFlushScheduler()
    assert isinstance(instance, FlushScheduler)


async def test_schedule_flush_fires_callback(
    scheduler: AsyncioFlushScheduler,
) -> None:
    called = asyncio.Event()

    async def _callback() -> None:
        called.set()

    scheduler.schedule_flush(0.01, _callback)
    await asyncio.wait_for(called.wait(), timeout=2.0)
    assert called.is_set()


async def test_cancel_flush_prevents_callback(
    scheduler: AsyncioFlushScheduler,
) -> None:
    called = asyncio.Event()

    async def _callback() -> None:
        called.set()

    handle = scheduler.schedule_flush(0.05, _callback)
    scheduler.cancel_flush(handle)
    await asyncio.sleep(0.1)
    assert not called.is_set()


async def test_multiple_flushes(
    scheduler: AsyncioFlushScheduler,
) -> None:
    results: list[int] = []

    async def _make_callback(n: int) -> None:
        results.append(n)

    scheduler.schedule_flush(0.01, lambda: _make_callback(1))
    scheduler.schedule_flush(0.02, lambda: _make_callback(2))

    await asyncio.sleep(0.15)
    assert 1 in results
    assert 2 in results


async def test_cancel_with_non_handle_is_noop(
    scheduler: AsyncioFlushScheduler,
) -> None:
    # cancel_flush with a non-TimerHandle is a silent no-op.
    scheduler.cancel_flush("not-a-handle")
