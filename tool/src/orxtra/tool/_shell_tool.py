from __future__ import annotations

import asyncio
import json
import shlex
import time
from pathlib import Path
from typing import Any

from orxtra.protocols._results import ExecResult, ToolOutput
from orxtra.protocols._tool import Tool, ToolError
from orxtra.tool._decorator import tool
from orxtra.tool._params import ShellBaseParams
from orxtra.tool._preview import check_and_preview
from orxtra.tool._renderers import JsonRenderer

_SIGTERM_GRACE_SECONDS = 5.0


@tool(
    "shell",
    "Run shell commands with a binary whitelist.",
    renderer=JsonRenderer(),
    namespace="exec",
    tags=frozenset({"mutation"}),
)
async def _shell_impl(
    params: ShellBaseParams,
    *,
    allowed_set: frozenset[str],
    read_root: Path,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
    env_filter: dict[str, str] | None,
) -> ToolOutput[ExecResult]:
    command = params.command
    requested_timeout = params.timeout if params.timeout is not None else timeout_ceiling

    tokens = shlex.split(command)
    if not tokens:
        msg = "Empty command"
        raise ToolError(msg)

    if tokens[0] not in allowed_set:
        msg = (
            f"Binary {tokens[0]!r} is not in the allowed set: "
            f"{sorted(allowed_set)}"
        )
        raise ToolError(msg)

    effective_timeout = min(requested_timeout, timeout_ceiling)

    env_kwargs: dict[str, Any] = {}
    if env_filter is not None:
        env_kwargs["env"] = env_filter

    start = time.monotonic()
    timed_out = False

    process = await asyncio.create_subprocess_exec(
        tokens[0],
        *tokens[1:],
        cwd=read_root,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        **env_kwargs,
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


def make_shell_tool(  # noqa: PLR0913
    allowed_binaries: list[str],
    description: str,
    read_root: Path,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
    env_filter: dict[str, str] | None = None,
) -> Tool:
    """Create a tool that runs shell commands with a binary whitelist.

    Args:
        allowed_binaries: List of permitted binary names.
        description: Human-readable description of what this tool does.
        read_root: Working directory for the subprocess.
        timeout_ceiling: Maximum allowed timeout in seconds.
        preview_threshold: Byte threshold for stdout/stderr preview.
        preview_lines: Number of head/tail lines in previews.
        env_filter: If not None, used as the subprocess environment.
            If None, the parent environment is inherited.

    Returns:
        A Tool instance for running shell commands.
    """
    _ = description  # Preserved for caller compatibility; @tool has its own
    return _shell_impl.bind(
        allowed_set=frozenset(allowed_binaries),
        read_root=read_root,
        timeout_ceiling=timeout_ceiling,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
        env_filter=env_filter,
    )
