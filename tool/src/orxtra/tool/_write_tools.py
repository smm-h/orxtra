"""File write tool constructors for the orxtra tool module."""

from __future__ import annotations

import asyncio
import shutil
from pathlib import Path
from typing import Any

from orxtra.protocols._results import Confirmation, ToolOutput
from orxtra.protocols._tool import Tool, ToolError
from orxtra.tool._decorator import tool
from orxtra.tool._params import (
    CopyParams,
    DeleteParams,
    EditParams,
    MkdirParams,
    MoveParams,
    MultiEditParams,
    SetExecutableParams,
    WriteParams,
)
from orxtra.tool._path import PathError, check_write_scope, resolve_and_check
from orxtra.tool._renderers import TextRenderer
from orxtra.tool._write_integration import safe_read_for_write, safe_write
from orxtra.write_safety import StaleWriteTracker, WriteQueue


def _path_error_to_tool_error(exc: PathError) -> ToolError:
    """Convert a PathError to a ToolError."""
    return ToolError(str(exc))


def _resolve_path(raw_path: str, root: Path) -> Path:
    """Resolve a path, converting PathError to ToolError."""
    try:
        return resolve_and_check(raw_path, root)
    except PathError as exc:
        raise _path_error_to_tool_error(exc) from exc


def _check_scope(resolved: Path, scope: list[Path] | None, root: Path) -> None:
    """Check write scope, converting PathError to ToolError."""
    try:
        check_write_scope(resolved, scope, root)
    except PathError as exc:
        raise _path_error_to_tool_error(exc) from exc


# ---------------------------------------------------------------------------
# Write tool
# ---------------------------------------------------------------------------


@tool("write", "Create or overwrite a file.", renderer=TextRenderer())
async def _write_impl(
    params: WriteParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Confirmation:
    resolved = _resolve_path(params.path, read_root)
    _check_scope(resolved, write_scope, read_root)

    create_dirs = params.create_dirs if params.create_dirs is not None else False
    if create_dirs:
        resolved.parent.mkdir(parents=True, exist_ok=True)

    is_new_file = not resolved.exists()
    await safe_write(
        resolved,
        params.content,
        queue,
        tracker,
        session_id,
        is_new_file=is_new_file,
    )
    return Confirmation(message=f"Wrote {resolved}")


def make_write_tool(
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Tool:
    """Construct the file write tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing writes.
    """
    return _write_impl.bind(
        read_root=read_root,
        write_scope=write_scope,
        queue=queue,
        tracker=tracker,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Edit tool
# ---------------------------------------------------------------------------


@tool("edit", "Find-and-replace in a file.", renderer=TextRenderer())
async def _edit_impl(
    params: EditParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Confirmation:
    resolved = _resolve_path(params.path, read_root)
    _check_scope(resolved, write_scope, read_root)

    old_string = params.old_string
    new_string = params.new_string
    replace_all = params.replace_all if params.replace_all is not None else False

    if not old_string:
        msg = "old_string must not be empty"
        raise ToolError(msg)

    content = await safe_read_for_write(resolved, queue, tracker, session_id)

    count = content.count(old_string)
    if count == 0:
        msg = f"old_string not found in {resolved}"
        raise ToolError(msg)

    if not replace_all and count > 1:
        msg = (
            f"Multiple matches ({count}) for old_string in {resolved}, "
            "use replace_all=true"
        )
        raise ToolError(msg)

    new_content = content.replace(old_string, new_string)
    await safe_write(
        resolved,
        new_content,
        queue,
        tracker,
        session_id,
        is_new_file=False,
    )

    if replace_all:
        return Confirmation(message=f"Edited {resolved} ({count} replacements)")
    return Confirmation(message=f"Edited {resolved}")


def make_edit_tool(
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Tool:
    """Construct the find-and-replace edit tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing writes.
    """
    return _edit_impl.bind(
        read_root=read_root,
        write_scope=write_scope,
        queue=queue,
        tracker=tracker,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Multi-edit tool
# ---------------------------------------------------------------------------


@tool("multi_edit", "Apply multiple find-and-replace edits in a batch.", renderer=TextRenderer())
async def _multi_edit_impl(
    params: MultiEditParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> ToolOutput[list[Confirmation]]:
    # Validate all paths upfront before applying any edits.
    resolved_edits: list[tuple[Path, str, str]] = []
    for i, edit in enumerate(params.edits):
        resolved = _resolve_path(edit.file, read_root)
        _check_scope(resolved, write_scope, read_root)
        old_string = edit.old_string
        if not old_string:
            msg = f"Edit {i}: old_string must not be empty"
            raise ToolError(msg)
        resolved_edits.append((resolved, old_string, edit.new_string))

    # Apply edits sequentially.
    confirmations: list[Confirmation] = []
    failures: list[str] = []
    for i, (resolved, old_string, new_string) in enumerate(resolved_edits):
        try:
            content = await safe_read_for_write(
                resolved, queue, tracker, session_id,
            )
            count = content.count(old_string)
            if count == 0:
                msg = f"Edit {i}: old_string not found in {resolved}"
                raise ToolError(msg)
            new_content = content.replace(old_string, new_string)
            await safe_write(
                resolved,
                new_content,
                queue,
                tracker,
                session_id,
                is_new_file=False,
            )
            confirmations.append(
                Confirmation(message=f"Edit {i}: edited {resolved}"),
            )
        except (ToolError, FileNotFoundError, OSError) as exc:
            failures.append(f"Edit {i}: {exc}")

    parts: list[str] = []
    for c in confirmations:
        parts.append(c.message)
    for f in failures:
        parts.append(f"FAILED - {f}")

    text = "\n".join(parts)
    if failures:
        text += f"\n{len(failures)} edit(s) failed, {len(confirmations)} succeeded"
    else:
        text += f"\nAll {len(confirmations)} edit(s) succeeded"

    return ToolOutput(data=confirmations, text=text)


def make_multi_edit_tool(
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Tool:
    """Construct the multi-edit tool for batched find-and-replace edits.

    Applies edits sequentially. If any edit fails, the result reports
    which edits succeeded and which failed (no atomic rollback).

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing writes.
    """
    return _multi_edit_impl.bind(
        read_root=read_root,
        write_scope=write_scope,
        queue=queue,
        tracker=tracker,
        session_id=session_id,
    )


# ---------------------------------------------------------------------------
# Mkdir tool
# ---------------------------------------------------------------------------


@tool("mkdir", "Create a directory.", renderer=TextRenderer())
async def _mkdir_impl(
    params: MkdirParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
) -> Confirmation:
    resolved = _resolve_path(params.path, read_root)
    _check_scope(resolved, write_scope, read_root)

    resolved.mkdir(parents=True, exist_ok=True)
    return Confirmation(message=f"Created {resolved}")


def make_mkdir_tool(
    read_root: Path,
    write_scope: list[Path] | None,
) -> Tool:
    """Construct the mkdir tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
    """
    return _mkdir_impl.bind(read_root=read_root, write_scope=write_scope)


# ---------------------------------------------------------------------------
# Move tool
# ---------------------------------------------------------------------------


@tool("move", "Move or rename a file or directory.", renderer=TextRenderer())
async def _move_impl(
    params: MoveParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
) -> Confirmation:
    resolved_src = _resolve_path(params.source, read_root)
    resolved_dst = _resolve_path(params.destination, read_root)
    _check_scope(resolved_src, write_scope, read_root)
    _check_scope(resolved_dst, write_scope, read_root)

    if not resolved_src.exists():
        msg = f"Source does not exist: {resolved_src}"
        raise ToolError(msg)

    resolved_src.rename(resolved_dst)
    return Confirmation(message=f"Moved {resolved_src} -> {resolved_dst}")


def make_move_tool(
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Tool:
    """Construct the move/rename tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing writes.
    """
    # queue, tracker, session_id captured for future write-safety integration.
    _ = queue, tracker, session_id
    return _move_impl.bind(read_root=read_root, write_scope=write_scope)


# ---------------------------------------------------------------------------
# Copy tool
# ---------------------------------------------------------------------------


@tool("copy", "Copy a file.", renderer=TextRenderer())
async def _copy_impl(
    params: CopyParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
) -> Confirmation:
    resolved_src = _resolve_path(params.source, read_root)
    resolved_dst = _resolve_path(params.destination, read_root)
    # Source is read-only: no write scope check.
    # Destination is a write: check write scope.
    _check_scope(resolved_dst, write_scope, read_root)

    if not resolved_src.exists():
        msg = f"Source does not exist: {resolved_src}"
        raise ToolError(msg)
    if not resolved_src.is_file():
        msg = f"Source is not a file: {resolved_src}"
        raise ToolError(msg)

    shutil.copy2(resolved_src, resolved_dst)
    return Confirmation(message=f"Copied {resolved_src} -> {resolved_dst}")


def make_copy_tool(
    read_root: Path,
    write_scope: list[Path] | None,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> Tool:
    """Construct the file copy tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
        queue: Per-path write queue for serialization.
        tracker: Stale-write detector.
        session_id: The session performing writes.
    """
    # queue, tracker, session_id captured for future write-safety integration.
    _ = queue, tracker, session_id
    return _copy_impl.bind(read_root=read_root, write_scope=write_scope)


# ---------------------------------------------------------------------------
# Delete tool
# ---------------------------------------------------------------------------


@tool("delete", "Delete a file or directory via saferm.", renderer=TextRenderer())
async def _delete_impl(
    params: DeleteParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
) -> Confirmation:
    resolved = _resolve_path(params.path, read_root)
    _check_scope(resolved, write_scope, read_root)

    if resolved.is_dir() and not params.recursive:
        msg = f"Path is a directory, set recursive=true to delete: {resolved}"
        raise ToolError(msg)

    cmd = [
        "saferm",
        "delete",
        "--description",
        params.description,
    ]
    if params.recursive:
        cmd.append("-r")
    cmd.append(str(resolved))

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    _, stderr_bytes = await proc.communicate()

    if proc.returncode != 0:
        stderr = stderr_bytes.decode("utf-8", errors="replace").strip()
        msg = f"saferm failed (exit {proc.returncode}): {stderr}"
        raise ToolError(msg)

    return Confirmation(message=f"Deleted {resolved}")


def make_delete_tool(
    read_root: Path,
    write_scope: list[Path] | None,
) -> Tool:
    """Construct the delete tool (wraps saferm).

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
    """
    return _delete_impl.bind(read_root=read_root, write_scope=write_scope)


# ---------------------------------------------------------------------------
# Set executable tool
# ---------------------------------------------------------------------------


@tool("set_executable", "Set the executable bit on a file.", renderer=TextRenderer())
async def _set_executable_impl(
    params: SetExecutableParams,
    *,
    read_root: Path,
    write_scope: list[Path] | None,
) -> Confirmation:
    resolved = _resolve_path(params.path, read_root)
    _check_scope(resolved, write_scope, read_root)

    if not resolved.exists():
        msg = f"File not found: {resolved}"
        raise ToolError(msg)
    if not resolved.is_file():
        msg = f"Not a file: {resolved}"
        raise ToolError(msg)

    resolved.chmod(resolved.stat().st_mode | 0o111)
    return Confirmation(message=f"Set executable: {resolved}")


def make_set_executable_tool(
    read_root: Path,
    write_scope: list[Path] | None,
) -> Tool:
    """Construct the set-executable tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
    """
    return _set_executable_impl.bind(read_root=read_root, write_scope=write_scope)
