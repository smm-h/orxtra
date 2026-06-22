from __future__ import annotations

import asyncio
import json
import shlex
import time
from typing import TYPE_CHECKING, Any

from orxt.protocols._tool import Tool, ToolError
from orxt.tool._preview import check_and_preview
from orxt.tool._validation import validate_args

if TYPE_CHECKING:
    from pathlib import Path

_SIGTERM_GRACE_SECONDS = 5.0


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

    The agent can run arbitrary commands, but only binaries in the whitelist
    are permitted. Non-zero exit codes are returned as data, not exceptions.

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
    allowed_set = frozenset(allowed_binaries)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Shell command to execute. "
                    "Only whitelisted binaries are allowed."
                ),
            },
            "timeout": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Timeout in seconds. Capped at the configured ceiling."
                ),
            },
        },
        "required": ["command"],
        "additionalProperties": False,
    }

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, schema)

        command: str = arguments["command"]
        requested_timeout: int = arguments.get("timeout", timeout_ceiling)

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

        result: dict[str, Any] = {
            "stdout": stdout_preview.content,
            "stderr": stderr_preview.content,
            "exit_code": process.returncode or 0,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
        }
        return json.dumps(result)

    return Tool(
        name="shell",
        description=description,
        parameters=schema,
        execute=execute,
    )
