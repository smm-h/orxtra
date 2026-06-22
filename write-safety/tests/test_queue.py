from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from orxtra.write_safety import WriteQueue

if TYPE_CHECKING:
    from pathlib import Path


async def test_acquire_release(tmp_path: Path) -> None:
    q = WriteQueue()
    target = tmp_path / "f.txt"
    await q.acquire(target)
    q.release(target)


async def test_lock_context_manager(tmp_path: Path) -> None:
    q = WriteQueue()
    target = tmp_path / "f.txt"
    async with q.lock(target):
        pass


async def test_same_path_serialized(tmp_path: Path) -> None:
    q = WriteQueue()
    target = tmp_path / "f.txt"
    order: list[int] = []

    async def worker(n: int) -> None:
        async with q.lock(target):
            order.append(n)
            await asyncio.sleep(0.01)

    t1 = asyncio.create_task(worker(1))
    await asyncio.sleep(0)  # let t1 start and acquire
    t2 = asyncio.create_task(worker(2))
    await asyncio.gather(t1, t2)
    assert order == [1, 2]


async def test_different_paths_no_contention(tmp_path: Path) -> None:
    q = WriteQueue()
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    entered: list[str] = []

    async def worker(path: Path, name: str) -> None:
        async with q.lock(path):
            entered.append(name)
            await asyncio.sleep(0.05)

    t1 = asyncio.create_task(worker(a, "a"))
    t2 = asyncio.create_task(worker(b, "b"))
    await asyncio.gather(t1, t2)
    # Both entered before either finished (concurrent, not serialized)
    assert entered == ["a", "b"]


async def test_path_canonicalization(tmp_path: Path) -> None:
    q = WriteQueue()
    absolute = tmp_path / "f.txt"
    relative_like = tmp_path / "." / "f.txt"

    order: list[int] = []

    async def worker(path: Path, n: int) -> None:
        async with q.lock(path):
            order.append(n)
            await asyncio.sleep(0.01)

    t1 = asyncio.create_task(worker(absolute, 1))
    await asyncio.sleep(0)
    t2 = asyncio.create_task(worker(relative_like, 2))
    await asyncio.gather(t1, t2)
    assert order == [1, 2]


async def test_stress_100_tasks(tmp_path: Path) -> None:
    q = WriteQueue()
    target = tmp_path / "f.txt"
    counter = 0

    async def worker() -> None:
        nonlocal counter
        async with q.lock(target):
            current = counter
            await asyncio.sleep(0)  # yield to event loop
            counter = current + 1

    tasks = [asyncio.create_task(worker()) for _ in range(100)]
    await asyncio.gather(*tasks)
    assert counter == 100


async def test_release_without_acquire(tmp_path: Path) -> None:
    q = WriteQueue()
    q.release(tmp_path / "never_acquired.txt")
