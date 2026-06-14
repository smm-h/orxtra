from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from orxt.write_safety import (
    StaleWriteError,
    StaleWriteTracker,
    WriteQueue,
    atomic_write,
    compute_hash,
)

if TYPE_CHECKING:
    from pathlib import Path


async def test_100_writers_same_file(tmp_path: Path) -> None:
    target = tmp_path / "shared.txt"
    q = WriteQueue()

    async def writer(value: str) -> None:
        async with q.lock(target):
            await atomic_write(target, value)

    tasks = [asyncio.create_task(writer(f"value-{i}")) for i in range(100)]
    await asyncio.gather(*tasks)

    content = target.read_text()
    assert content.startswith("value-")
    assert content in {f"value-{i}" for i in range(100)}


async def test_100_writers_10_files(tmp_path: Path) -> None:
    q = WriteQueue()
    files = [tmp_path / f"file-{i}.txt" for i in range(10)]

    async def writer(file_idx: int, value: str) -> None:
        target = files[file_idx]
        async with q.lock(target):
            await atomic_write(target, value)

    tasks = [
        asyncio.create_task(writer(i % 10, f"writer-{i}"))
        for i in range(100)
    ]
    await asyncio.gather(*tasks)

    for f in files:
        content = f.read_text()
        assert content.startswith("writer-")


async def test_read_write_interleaving(tmp_path: Path) -> None:
    target = tmp_path / "rw.txt"
    await atomic_write(target, "initial")
    q = WriteQueue()

    read_results: list[str] = []

    async def reader() -> None:
        for _ in range(5):
            content = target.read_text()
            assert len(content) > 0
            read_results.append(content)
            await asyncio.sleep(0)

    async def writer(value: str) -> None:
        async with q.lock(target):
            await atomic_write(target, value)

    reader_tasks = [asyncio.create_task(reader()) for _ in range(50)]
    writer_tasks = [
        asyncio.create_task(writer(f"write-{i}")) for i in range(50)
    ]
    await asyncio.gather(*reader_tasks, *writer_tasks)

    for r in read_results:
        assert r == "initial" or r.startswith("write-")


async def test_stale_detection_under_contention(tmp_path: Path) -> None:
    target = tmp_path / "stale.txt"
    target.write_text("original")
    original_hash = compute_hash(target)

    tracker = StaleWriteTracker()
    q = WriteQueue()
    results: list[str] = []

    for i in range(10):
        tracker.record_read(f"s{i}", target, original_hash)

    async def session_write(session_id: str, value: str) -> None:
        async with q.lock(target):
            current_hash = compute_hash(target)
            try:
                tracker.check_write(session_id, target, current_hash)
            except StaleWriteError:
                results.append("stale")
                return
            await atomic_write(target, value)
            new_hash = compute_hash(target)
            for i in range(10):
                sid = f"s{i}"
                if sid == session_id:
                    tracker.record_read(sid, target, new_hash)

            results.append("ok")

    tasks = [
        asyncio.create_task(session_write(f"s{i}", f"value-{i}"))
        for i in range(10)
    ]
    await asyncio.gather(*tasks)

    assert results.count("ok") == 1
    assert results.count("stale") == 9
