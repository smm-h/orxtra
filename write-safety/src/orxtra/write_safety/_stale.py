from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path


class StaleWriteError(Exception):
    """Raised when a write would silently overwrite another session's changes."""


def compute_hash(path: Path) -> str:
    """Return SHA-256 hex digest of a file's contents."""
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(8192):
            h.update(chunk)
    return h.hexdigest()


class StaleWriteTracker:
    """Tracks content hashes per session for stale-write detection."""

    def __init__(self) -> None:
        self._reads: dict[Path, dict[str, str]] = {}

    def record_read(self, session_id: str, path: Path, content_hash: str) -> None:
        """Record the content hash that a session observed for a path."""
        canonical = path.resolve()
        if canonical not in self._reads:
            self._reads[canonical] = {}
        self._reads[canonical][session_id] = content_hash

    def check_write(
        self,
        session_id: str,
        path: Path,
        current_hash: str,
        *,
        is_new_file: bool = False,
    ) -> None:
        """Check if a write is safe. Raises StaleWriteError if not.

        Args:
            session_id: The session attempting the write.
            path: The file path being written.
            current_hash: The current content hash of the file on disk.
            is_new_file: If True, skip the "has this session read this path" check.
        """
        canonical = path.resolve()

        if is_new_file:
            return

        session_reads = self._reads.get(canonical)
        if session_reads is None or session_id not in session_reads:
            msg = (
                f"Session {session_id!r} has never read {path}"
                " -- cannot write"
            )
            raise StaleWriteError(msg)

        recorded_hash = session_reads[session_id]
        if recorded_hash != current_hash:
            msg = (
                f"File {path} changed since session {session_id!r}"
                f" last read it (expected {recorded_hash[:12]}...,"
                f" got {current_hash[:12]}...)"
            )
            raise StaleWriteError(msg)
