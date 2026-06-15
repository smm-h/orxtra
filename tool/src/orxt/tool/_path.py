from __future__ import annotations

import os
from pathlib import Path


class PathError(Exception):
    """Raised when a path violates containment or scope rules."""


def resolve_and_check(raw_path: str, root: Path) -> Path:
    """Resolve raw_path against root, canonicalize, check containment.

    Args:
        raw_path: The raw path string from the tool caller.
        root: The boundary root (must already be resolved/canonical).

    Returns:
        The resolved, canonical path.

    Raises:
        PathError: If the path is empty, or escapes the root boundary.
    """
    if not raw_path:
        msg = "Path must not be empty"
        raise PathError(msg)

    resolved = (root / raw_path).resolve()
    root_str = str(root)

    if resolved == root:
        return resolved

    if not str(resolved).startswith(root_str + os.sep):
        msg = f"Path {raw_path!r} escapes root boundary {root}"
        raise PathError(msg)

    return resolved


def check_write_scope(
    resolved: Path,
    scope: list[Path] | None,
    root: Path,
) -> None:
    """Check if resolved path is within write scope.

    Args:
        resolved: Already-resolved path (from resolve_and_check).
        scope: List of allowed write paths, or None for unrestricted.
        root: The read root boundary (for error messages).

    Raises:
        PathError: If the path is outside all scope paths.
    """
    if scope is None:
        return

    for scope_path in scope:
        canonical_scope = scope_path.resolve()
        if resolved == canonical_scope:
            return
        if str(resolved).startswith(str(canonical_scope) + os.sep):
            return

    msg = f"Path {resolved} is outside write scope"
    raise PathError(msg)
