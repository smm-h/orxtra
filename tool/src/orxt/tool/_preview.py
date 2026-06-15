from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PreviewResult:
    """Result of a preview check on tool output content."""

    content: str
    is_preview: bool
    total_lines: int
    total_bytes: int


def check_and_preview(
    content: str,
    threshold: int,
    preview_lines: int,
) -> PreviewResult:
    """If content exceeds threshold bytes, return head/tail preview.

    Args:
        content: The full content to potentially preview.
        threshold: Byte size threshold. Content at or under this is returned in full.
        preview_lines: Number of lines to show at head and tail of the preview.

    Returns:
        PreviewResult with either full content or a head/tail preview.
    """
    total_bytes = len(content.encode("utf-8"))
    lines = content.splitlines(keepends=True)
    total_lines = len(lines)

    if total_bytes <= threshold:
        return PreviewResult(
            content=content,
            is_preview=False,
            total_lines=total_lines,
            total_bytes=total_bytes,
        )

    head = lines[:preview_lines]
    tail = lines[-preview_lines:] if len(lines) > preview_lines * 2 else lines[preview_lines:]

    omitted = total_lines - len(head) - len(tail)
    separator = f"\n... ({omitted} lines omitted, {total_lines} total lines, {total_bytes} total bytes) ...\n"

    preview_content = "".join(head) + separator + "".join(tail)

    return PreviewResult(
        content=preview_content,
        is_preview=True,
        total_lines=total_lines,
        total_bytes=total_bytes,
    )


class FullRetrievalGuard:
    """Tracks which paths have received previews per session.

    ``full=true`` on a tool call is only honored if the session already
    received a preview for that path. This prevents agents from bypassing
    the preview system by always requesting full content.
    """

    def __init__(self) -> None:
        self._previews: dict[str, set[str]] = {}

    def record_preview(self, session_id: str, path: str) -> None:
        """Record that a session received a preview for a path."""
        if session_id not in self._previews:
            self._previews[session_id] = set()
        self._previews[session_id].add(path)

    def check_full_allowed(self, session_id: str, path: str) -> bool:
        """Check if a session is allowed to request full content for a path.

        Returns True only if the session previously received a preview.
        """
        session_paths = self._previews.get(session_id)
        if session_paths is None:
            return False
        return path in session_paths
