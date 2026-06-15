"""File write tool constructors for the orxt tool module."""

from __future__ import annotations

import asyncio
import os
import shutil
from typing import TYPE_CHECKING, Any

from orxt.protocols._tool import Tool, ToolError
from orxt.tool._path import PathError, check_write_scope, resolve_and_check
from orxt.tool._validation import validate_args
from orxt.tool._write_integration import safe_read_for_write, safe_write

if TYPE_CHECKING:
    from pathlib import Path

    from orxt.write_safety import StaleWriteTracker, WriteQueue


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
# Schema definitions
# ---------------------------------------------------------------------------

_WRITE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "content": {"type": "string"},
        "create_dirs": {"type": "boolean"},
    },
    "required": ["path", "content"],
    "additionalProperties": False,
}

_EDIT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "old_string": {"type": "string"},
        "new_string": {"type": "string"},
        "replace_all": {"type": "boolean"},
    },
    "required": ["path", "old_string", "new_string"],
    "additionalProperties": False,
}

_MKDIR_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
    },
    "required": ["path"],
    "additionalProperties": False,
}

_MOVE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "destination": {"type": "string"},
    },
    "required": ["source", "destination"],
    "additionalProperties": False,
}

_COPY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "source": {"type": "string"},
        "destination": {"type": "string"},
    },
    "required": ["source", "destination"],
    "additionalProperties": False,
}

_DELETE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
        "description": {"type": "string"},
        "recursive": {"type": "boolean"},
    },
    "required": ["path", "description", "recursive"],
    "additionalProperties": False,
}

_SET_EXECUTABLE_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "path": {"type": "string"},
    },
    "required": ["path"],
    "additionalProperties": False,
}


# ---------------------------------------------------------------------------
# Tool constructors
# ---------------------------------------------------------------------------


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

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _WRITE_SCHEMA)
        resolved = _resolve_path(args["path"], read_root)
        _check_scope(resolved, write_scope, read_root)

        create_dirs = args.get("create_dirs", False)
        if create_dirs:
            resolved.parent.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240

        is_new_file = not resolved.exists()  # noqa: ASYNC240
        await safe_write(
            resolved,
            args["content"],
            queue,
            tracker,
            session_id,
            is_new_file=is_new_file,
        )
        return f"Wrote {resolved}"

    return Tool(
        name="write",
        description="Create or overwrite a file.",
        parameters=_WRITE_SCHEMA,
        execute=execute,
    )


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

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _EDIT_SCHEMA)
        resolved = _resolve_path(args["path"], read_root)
        _check_scope(resolved, write_scope, read_root)

        old_string = args["old_string"]
        new_string = args["new_string"]
        replace_all = args.get("replace_all", False)

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
            return f"Edited {resolved} ({count} replacements)"
        return f"Edited {resolved}"

    return Tool(
        name="edit",
        description="Find-and-replace in a file.",
        parameters=_EDIT_SCHEMA,
        execute=execute,
    )


def make_mkdir_tool(
    read_root: Path,
    write_scope: list[Path] | None,
) -> Tool:
    """Construct the mkdir tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
    """

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _MKDIR_SCHEMA)
        resolved = _resolve_path(args["path"], read_root)
        _check_scope(resolved, write_scope, read_root)

        resolved.mkdir(parents=True, exist_ok=True)  # noqa: ASYNC240
        return f"Created {resolved}"

    return Tool(
        name="mkdir",
        description="Create a directory.",
        parameters=_MKDIR_SCHEMA,
        execute=execute,
    )


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
    # Suppress unused-argument warnings: queue, tracker, and session_id
    # are captured for future write-safety integration on move operations.
    _ = queue, tracker, session_id

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _MOVE_SCHEMA)
        resolved_src = _resolve_path(args["source"], read_root)
        resolved_dst = _resolve_path(args["destination"], read_root)
        _check_scope(resolved_src, write_scope, read_root)
        _check_scope(resolved_dst, write_scope, read_root)

        if not resolved_src.exists():  # noqa: ASYNC240
            msg = f"Source does not exist: {resolved_src}"
            raise ToolError(msg)

        resolved_src.rename(resolved_dst)  # noqa: ASYNC240
        return f"Moved {resolved_src} -> {resolved_dst}"

    return Tool(
        name="move",
        description="Move or rename a file or directory.",
        parameters=_MOVE_SCHEMA,
        execute=execute,
    )


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
    # Suppress unused-argument warnings: queue, tracker, and session_id
    # are captured for future write-safety integration on copy operations.
    _ = queue, tracker, session_id

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _COPY_SCHEMA)
        resolved_src = _resolve_path(args["source"], read_root)
        resolved_dst = _resolve_path(args["destination"], read_root)
        # Source is read-only: no write scope check.
        # Destination is a write: check write scope.
        _check_scope(resolved_dst, write_scope, read_root)

        if not resolved_src.exists():  # noqa: ASYNC240
            msg = f"Source does not exist: {resolved_src}"
            raise ToolError(msg)
        if not resolved_src.is_file():  # noqa: ASYNC240
            msg = f"Source is not a file: {resolved_src}"
            raise ToolError(msg)

        shutil.copy2(resolved_src, resolved_dst)  # noqa: ASYNC240
        return f"Copied {resolved_src} -> {resolved_dst}"

    return Tool(
        name="copy",
        description="Copy a file.",
        parameters=_COPY_SCHEMA,
        execute=execute,
    )


def make_delete_tool(
    read_root: Path,
    write_scope: list[Path] | None,
) -> Tool:
    """Construct the delete tool (wraps saferm).

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
    """

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _DELETE_SCHEMA)
        resolved = _resolve_path(args["path"], read_root)
        _check_scope(resolved, write_scope, read_root)

        recursive = args["recursive"]
        if resolved.is_dir() and not recursive:  # noqa: ASYNC240
            msg = f"Path is a directory, set recursive=true to delete: {resolved}"
            raise ToolError(msg)

        cmd = [
            "saferm",
            "delete",
            "--description",
            args["description"],
        ]
        if recursive:
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

        return f"Deleted {resolved}"

    return Tool(
        name="delete",
        description="Delete a file or directory via saferm.",
        parameters=_DELETE_SCHEMA,
        execute=execute,
    )


def make_set_executable_tool(
    read_root: Path,
    write_scope: list[Path] | None,
) -> Tool:
    """Construct the set-executable tool.

    Args:
        read_root: Root directory for path containment.
        write_scope: Allowed write paths, or None for unrestricted.
    """

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, _SET_EXECUTABLE_SCHEMA)
        resolved = _resolve_path(args["path"], read_root)
        _check_scope(resolved, write_scope, read_root)

        if not resolved.exists():  # noqa: ASYNC240
            msg = f"File not found: {resolved}"
            raise ToolError(msg)
        if not resolved.is_file():  # noqa: ASYNC240
            msg = f"Not a file: {resolved}"
            raise ToolError(msg)

        os.chmod(resolved, resolved.stat().st_mode | 0o111)  # noqa: ASYNC240
        return f"Set executable: {resolved}"

    return Tool(
        name="set_executable",
        description="Set the executable bit on a file.",
        parameters=_SET_EXECUTABLE_SCHEMA,
        execute=execute,
    )
