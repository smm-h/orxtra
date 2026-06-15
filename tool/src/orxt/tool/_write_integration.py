from __future__ import annotations

from typing import TYPE_CHECKING

from orxt.write_safety import (
    StaleWriteTracker,
    WriteQueue,
    atomic_write,
    compute_hash,
)

if TYPE_CHECKING:
    from pathlib import Path


async def safe_write(  # noqa: PLR0913
    path: Path,
    content: str | bytes,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
    *,
    is_new_file: bool,
) -> None:
    """Lock, stale check, atomic write, release.

    Args:
        path: Target file path.
        content: Content to write.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing the write.
        is_new_file: If True, skip stale-write detection (file doesn't exist yet).
    """
    async with queue.lock(path):
        if not is_new_file:
            current_hash = compute_hash(path)
            tracker.check_write(
                session_id,
                path,
                current_hash,
                is_new_file=False,
            )

        await atomic_write(path, content)

        # After writing, record the new hash so subsequent writes
        # by the same session don't trigger stale detection.
        new_hash = compute_hash(path)
        tracker.record_read(session_id, path, new_hash)


async def safe_read_for_write(
    path: Path,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> str:
    """Lock, read, record hash, release. For edit's read-modify-write.

    Args:
        path: File path to read.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing the read.

    Returns:
        The file contents as a string.
    """
    async with queue.lock(path):
        content = path.read_text(encoding="utf-8")  # noqa: ASYNC240
        content_hash = compute_hash(path)
        tracker.record_read(session_id, path, content_hash)
        return content
