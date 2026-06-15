from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any

from orxt.protocols._tool import Tool, ToolError
from orxt.tool._validation import validate_args

if TYPE_CHECKING:
    from pathlib import Path

_TIMEOUT_SECONDS = 30
_MIN_COMMIT_ARGS = 2  # message + at least one file

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "subcommand": {"type": "string", "description": "Git subcommand to run"},
        "args": {
            "type": "array",
            "items": {"type": "string"},
            "description": (
                "Arguments. For commit: first element is message,"
                " rest are file paths."
            ),
        },
    },
    "required": ["subcommand"],
    "additionalProperties": False,
}

_DESCRIPTION = (
    "Run git operations. Read-only subcommands return output directly."
    " Mutation subcommands (commit) use safegit for concurrency safety."
    " For commit, the args array format is: first element is the commit"
    " message, remaining elements are file paths."
)


async def _run_git(
    cmd: list[str],
    cwd: Path,
) -> tuple[str, str, int]:
    """Run a git subprocess and return (stdout, stderr, returncode).

    Raises ToolError on timeout.
    """
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            proc.communicate(),
            timeout=_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        proc.kill()
        await proc.wait()
        msg = "Git command timed out"
        raise ToolError(msg) from None
    return (
        stdout_bytes.decode().strip(),
        stderr_bytes.decode().strip(),
        proc.returncode or 0,
    )


def _format_output(stdout: str, stderr: str, returncode: int) -> str:
    """Format read-only command output. Returns stderr on failure."""
    if returncode != 0:
        return stderr or f"(exit code {returncode})"
    return stdout or "(no output)"


async def _handle_status(_args: list[str], cwd: Path) -> str:
    stdout, stderr, rc = await _run_git(["git", "status", "--porcelain"], cwd)
    return _format_output(stdout, stderr, rc)


async def _handle_diff(args: list[str], cwd: Path) -> str:
    cmd = ["git", "diff", *args]
    stdout, stderr, rc = await _run_git(cmd, cwd)
    return _format_output(stdout, stderr, rc)


async def _handle_log(args: list[str], cwd: Path) -> str:
    cmd = ["git", "log", *args] if args else ["git", "log", "--oneline", "-20"]
    stdout, stderr, rc = await _run_git(cmd, cwd)
    return _format_output(stdout, stderr, rc)


async def _handle_show(args: list[str], cwd: Path) -> str:
    cmd = ["git", "show", *args]
    stdout, stderr, rc = await _run_git(cmd, cwd)
    return _format_output(stdout, stderr, rc)


async def _handle_blame(args: list[str], cwd: Path) -> str:
    cmd = ["git", "blame", *args]
    stdout, stderr, rc = await _run_git(cmd, cwd)
    return _format_output(stdout, stderr, rc)


async def _handle_branches(_args: list[str], cwd: Path) -> str:
    stdout, stderr, rc = await _run_git(["git", "branch", "-a"], cwd)
    return _format_output(stdout, stderr, rc)


async def _handle_changed_files(_args: list[str], cwd: Path) -> str:
    stdout, stderr, rc = await _run_git(
        ["git", "diff", "--name-only", "HEAD"], cwd
    )
    return _format_output(stdout, stderr, rc)


async def _handle_commit(
    args: list[str],
    cwd: Path,
    run_context: dict[str, str] | None,
) -> str:
    """Run safegit commit. Raises ToolError on validation or exec failure."""
    if not args or not args[0]:
        msg = "Commit message must be non-empty"
        raise ToolError(msg)
    if len(args) < _MIN_COMMIT_ARGS:
        msg = "Commit requires at least one file path after the message"
        raise ToolError(msg)

    message = args[0]
    files = args[1:]

    cmd: list[str] = ["safegit", "commit", "-m", message]
    if run_context:
        for key, value in run_context.items():
            cmd.extend(["--trailer", f"{key}: {value}"])
    cmd.append("--")
    cmd.extend(files)

    stdout, stderr, rc = await _run_git(cmd, cwd)
    if rc != 0:
        raise ToolError(stderr or f"safegit commit failed (exit code {rc})")
    return stdout or "(no output)"


_READ_HANDLERS: dict[str, Any] = {
    "status": _handle_status,
    "diff": _handle_diff,
    "log": _handle_log,
    "show": _handle_show,
    "blame": _handle_blame,
    "branches": _handle_branches,
    "changed_files": _handle_changed_files,
}

_MUTATION_SUBCOMMANDS: set[str] = {"commit"}

_ALL_SUBCOMMANDS: set[str] = {*_READ_HANDLERS, *_MUTATION_SUBCOMMANDS}


def make_git_tool(
    read_root: Path,
    allowed_subcommands: list[str],
    run_context: dict[str, str] | None = None,
) -> Tool:
    """Create a git tool with the specified allowed subcommands.

    Args:
        read_root: Working directory for git commands.
        allowed_subcommands: Which subcommands the agent may use.
        run_context: Optional key-value pairs added as trailers on commits.

    Returns:
        A Tool instance for git operations.

    Raises:
        ValueError: If any allowed subcommand is not recognized.
    """
    unknown = set(allowed_subcommands) - _ALL_SUBCOMMANDS
    if unknown:
        msg = f"Unknown git subcommands: {', '.join(sorted(unknown))}"
        raise ValueError(msg)

    allowed = set(allowed_subcommands)

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _PARAMETERS)

        subcommand: str = arguments["subcommand"]
        args: list[str] = arguments.get("args", [])

        if subcommand not in allowed:
            msg = f"Subcommand '{subcommand}' is not allowed"
            raise ToolError(msg)

        if subcommand in _READ_HANDLERS:
            handler = _READ_HANDLERS[subcommand]
            result: str = await handler(args, read_root)
            return result

        if subcommand == "commit":
            return await _handle_commit(args, read_root, run_context)

        # Should be unreachable due to the allowed check above
        msg = f"No handler for subcommand '{subcommand}'"
        raise ToolError(msg)

    return Tool(
        name="git",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        execute=execute,
    )
