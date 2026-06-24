from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Any

from orxtra.protocols._results import ExecResult, ToolOutput
from orxtra.protocols._tool import Tool, ToolError
from orxtra.tool._decorator import tool
from orxtra.tool._params import ExecBaseParams
from orxtra.tool._path import PathError, resolve_and_check
from orxtra.tool._preview import check_and_preview
from orxtra.tool._renderers import JsonRenderer

_SIGTERM_GRACE_SECONDS = 5.0

# Characters that are dangerous in shell contexts. Even though subprocess_exec
# doesn't interpret them, rejecting them is defense-in-depth against accidental
# shell invocation or downstream misuse.
_SHELL_METACHAR_PATTERN = re.compile(r"\.\.|~|\$|`")


def _validate_exec_arg(arg: str, read_root: Path) -> None:
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


@tool(
    "exec",
    "Run a fixed executable with arguments.",
    renderer=JsonRenderer(),
    namespace="exec",
    tags=frozenset({"mutation"}),
)
async def _exec_impl(
    params: ExecBaseParams,
    *,
    executable: str,
    read_root: Path,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
    arg_validation: bool,
) -> ToolOutput[ExecResult]:
    cmd_args: list[str] = params.args if params.args is not None else []

    if arg_validation:
        for arg in cmd_args:
            _validate_exec_arg(arg, read_root)

    requested_timeout = params.timeout if params.timeout is not None else timeout_ceiling
    effective_timeout = min(requested_timeout, timeout_ceiling)

    start = time.monotonic()
    timed_out = False

    process = await asyncio.create_subprocess_exec(
        executable,
        *cmd_args,
        cwd=read_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=effective_timeout,
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
    return ToolOutput(
        data=ExecResult(
            stdout=stdout, stderr=stderr,
            exit_code=exit_code, timed_out=timed_out,
            duration_ms=duration_ms,
        ),
        text=json.dumps(result_dict),
    )


def make_exec_tool(  # noqa: PLR0913
    executable: str,
    description: str,
    read_root: Path,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
    *,
    arg_validation: bool = True,
) -> Tool:
    """Create a tool that runs a single fixed executable.

    The agent can pass arguments and a timeout, but cannot control which
    binary runs. Non-zero exit codes are returned as data, not exceptions.

    Args:
        executable: The binary to run (e.g. "pytest", "uv").
        description: Human-readable description of what this tool does.
        read_root: Working directory for the subprocess.
        timeout_ceiling: Maximum allowed timeout in seconds.
        preview_threshold: Byte threshold for stdout/stderr preview.
        preview_lines: Number of head/tail lines in previews.
        arg_validation: When True (default), validate each string argument
            for path containment and shell metacharacters.

    Returns:
        A Tool instance for running the executable.
    """
    _ = description  # Preserved for caller compatibility; @tool has its own
    return _exec_impl.bind(
        name=executable,
        executable=executable,
        read_root=read_root,
        timeout_ceiling=timeout_ceiling,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
        arg_validation=arg_validation,
    )
