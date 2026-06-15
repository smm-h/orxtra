from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from orxt.write_safety import StaleWriteError, StaleWriteTracker, WriteQueue
from orxt.tool._write_integration import safe_read_for_write, safe_write


@pytest.fixture
def queue() -> WriteQueue:
    return WriteQueue()


@pytest.fixture
def tracker() -> StaleWriteTracker:
    return StaleWriteTracker()


class TestSafeWrite:
    """Tests for safe_write."""

    @pytest.mark.asyncio
    async def test_write_new_file(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Writing a new file with is_new_file=True succeeds."""
        target = tmp_path / "new.txt"
        await safe_write(
            target, "hello", queue, tracker, "session1", is_new_file=True,
        )
        assert target.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_write_existing_without_read_raises(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Writing an existing file without reading first raises StaleWriteError."""
        target = tmp_path / "existing.txt"
        target.write_text("original")
        with pytest.raises(StaleWriteError):
            await safe_write(
                target, "new content", queue, tracker, "session1",
                is_new_file=False,
            )

    @pytest.mark.asyncio
    async def test_write_after_read_succeeds(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Writing after safe_read_for_write succeeds."""
        target = tmp_path / "file.txt"
        target.write_text("original")
        await safe_read_for_write(target, queue, tracker, "session1")
        await safe_write(
            target, "updated", queue, tracker, "session1", is_new_file=False,
        )
        assert target.read_text() == "updated"

    @pytest.mark.asyncio
    async def test_concurrent_writes_serialized(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Concurrent writes to the same path are serialized."""
        target = tmp_path / "file.txt"
        order: list[str] = []

        async def writer(name: str, delay: float) -> None:
            await safe_write(
                target, f"content-{name}", queue, tracker, name,
                is_new_file=True,
            )
            order.append(name)

        # Both start concurrently; the lock serializes them
        await asyncio.gather(writer("a", 0.0), writer("b", 0.0))
        # Both should complete (order may vary, but both ran)
        assert len(order) == 2
        assert set(order) == {"a", "b"}

    @pytest.mark.asyncio
    async def test_write_binary_content(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Writing binary content works."""
        target = tmp_path / "binary.bin"
        data = b"\x00\x01\x02\xff"
        await safe_write(
            target, data, queue, tracker, "session1", is_new_file=True,
        )
        assert target.read_bytes() == data

    @pytest.mark.asyncio
    async def test_write_nonexistent_parent_raises(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Writing to a path with non-existent parent directory raises."""
        target = tmp_path / "no_such_dir" / "file.txt"
        with pytest.raises(FileNotFoundError):
            await safe_write(
                target, "content", queue, tracker, "session1",
                is_new_file=True,
            )

    @pytest.mark.asyncio
    async def test_stale_write_cross_session(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Session A reads, session B writes, session A writes -> StaleWriteError for A."""
        target = tmp_path / "file.txt"
        target.write_text("original")

        # Session A reads
        await safe_read_for_write(target, queue, tracker, "session_a")

        # Session B reads and writes (changes the file)
        await safe_read_for_write(target, queue, tracker, "session_b")
        await safe_write(
            target, "modified by b", queue, tracker, "session_b",
            is_new_file=False,
        )

        # Session A tries to write -- file changed since A read it
        with pytest.raises(StaleWriteError):
            await safe_write(
                target, "modified by a", queue, tracker, "session_a",
                is_new_file=False,
            )

    @pytest.mark.asyncio
    async def test_new_file_skips_stale_check(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """is_new_file=True skips stale check even without prior read."""
        target = tmp_path / "brand_new.txt"
        # No read, but is_new_file=True should skip the check
        await safe_write(
            target, "hello", queue, tracker, "session1", is_new_file=True,
        )
        assert target.read_text() == "hello"

    @pytest.mark.asyncio
    async def test_consecutive_writes_same_session(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """Same session can write twice: safe_write records new hash after write."""
        target = tmp_path / "file.txt"
        await safe_write(
            target, "first", queue, tracker, "session1", is_new_file=True,
        )
        # Second write should succeed because safe_write recorded the new hash
        await safe_write(
            target, "second", queue, tracker, "session1", is_new_file=False,
        )
        assert target.read_text() == "second"


class TestSafeReadForWrite:
    """Tests for safe_read_for_write."""

    @pytest.mark.asyncio
    async def test_read_returns_content(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """safe_read_for_write returns the file content."""
        target = tmp_path / "file.txt"
        target.write_text("hello world")
        content = await safe_read_for_write(target, queue, tracker, "session1")
        assert content == "hello world"

    @pytest.mark.asyncio
    async def test_read_records_hash(
        self, tmp_path: Path, queue: WriteQueue, tracker: StaleWriteTracker,
    ) -> None:
        """After reading, write is allowed (hash was recorded)."""
        target = tmp_path / "file.txt"
        target.write_text("content")
        await safe_read_for_write(target, queue, tracker, "session1")
        # If hash wasn't recorded, this would raise StaleWriteError
        await safe_write(
            target, "new content", queue, tracker, "session1",
            is_new_file=False,
        )
        assert target.read_text() == "new content"
