"""Reusable subprocess execution machinery.

Relocated from ``_exec_tool.py`` to serve both the exec tool and the
monty command capability.  The core logic is identical: fork a pinned
executable with validated arguments, enforce a timeout via
SIGTERM/SIGKILL, and return structured output.
"""

from __future__ import annotations

import asyncio
import re
import time
from pathlib import Path
from typing import Any

from orxtra.protocols import ExecResult, ToolError, ToolOutput
from orxtra.tool._path import PathError, resolve_and_check
from orxtra.tool._preview import check_and_preview

_SIGTERM_GRACE_SECONDS = 5.0

# Characters that are dangerous in shell contexts. Even though subprocess_exec
# doesn't interpret them, rejecting them is defense-in-depth against accidental
# shell invocation or downstream misuse.
_SHELL_METACHAR_PATTERN = re.compile(r"\.\.|~|\$|`")


def validate_exec_arg(arg: str, read_root: Path) -> None:
    """Validate a single exec tool argument for safety.

    Checks:
    1. Reject shell metacharacters (defense-in-depth).
    2. If the arg looks like a path (contains ``/`` or ``\\``), verify it
       resolves within ``read_root``.

    Raises:
        ToolError: If the argument fails validation.
    """
    if _SHELL_METACHAR_PATTERN.search(arg):
        msg = f"Argument contains forbidden characters: {arg!r}"
        raise ToolError(msg)

    if "/" in arg or "\\" in arg:
        try:
            resolve_and_check(arg, read_root)
        except PathError as exc:
            msg = f"Path-like argument escapes read root: {arg!r}"
            raise ToolError(msg) from exc


async def run_subprocess(  # noqa: PLR0913
    *,
    executable: str,
    args: list[str],
    cwd: Path,
    timeout: int,
    arg_validation: bool,
    preview_threshold: int,
    preview_lines: int,
) -> ToolOutput[ExecResult]:
    """Run a subprocess with timeout enforcement and output capture.

    Args:
        executable: The binary to run.
        args: Command-line arguments.
        cwd: Working directory for the subprocess.
        timeout: Maximum execution time in seconds.
        arg_validation: When True, validate each argument for safety.
        preview_threshold: Byte threshold for stdout/stderr preview.
        preview_lines: Number of head/tail lines in previews.

    Returns:
        A ToolOutput containing the ExecResult.
    """
    if arg_validation:
        for arg in args:
            validate_exec_arg(arg, cwd)

    start = time.monotonic()
    timed_out = False

    process = await asyncio.create_subprocess_exec(
        executable,
        *args,
        cwd=cwd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=timeout,
        )
    except TimeoutError:
        timed_out = True
        process.terminate()
        try:
            await asyncio.wait_for(
                process.wait(),
                timeout=_SIGTERM_GRACE_SECONDS,
            )
        except TimeoutError:
            process.kill()
            await process.wait()
        stdout_bytes = b""
        stderr_bytes = b""

    duration_ms = int((time.monotonic() - start) * 1000)

    stdout = stdout_bytes.decode(errors="replace")
    stderr = stderr_bytes.decode(errors="replace")

    stdout_preview = check_and_preview(stdout, preview_threshold, preview_lines)
    stderr_preview = check_and_preview(stderr, preview_threshold, preview_lines)

    exit_code = process.returncode or 0
    result_dict: dict[str, Any] = {
        "stdout": stdout_preview.content,
        "stderr": stderr_preview.content,
        "exit_code": exit_code,
        "timed_out": timed_out,
        "duration_ms": duration_ms,
    }

    import json  # noqa: PLC0415

    return ToolOutput(
        data=ExecResult(
            stdout=stdout, stderr=stderr,
            exit_code=exit_code, timed_out=timed_out,
            duration_ms=duration_ms,
        ),
        text=json.dumps(result_dict),
    )
