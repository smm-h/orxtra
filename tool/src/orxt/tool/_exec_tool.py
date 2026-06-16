from __future__ import annotations

import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from orxt.protocols._tool import Tool
from orxt.tool._preview import check_and_preview
from orxt.tool._validation import validate_args

if TYPE_CHECKING:
    from pathlib import Path

_BASE_PROPERTIES: dict[str, Any] = {
    "args": {
        "type": "array",
        "items": {"type": "string"},
        "description": "Command-line arguments to pass to the executable.",
    },
    "timeout": {
        "type": "integer",
        "minimum": 1,
        "description": "Timeout in seconds. Capped at the configured ceiling.",
    },
}

_SIGTERM_GRACE_SECONDS = 5.0


def make_exec_tool(  # noqa: PLR0913
    executable: str,
    description: str,
    arg_schema: dict[str, Any],
    read_root: Path,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
) -> Tool:
    """Create a tool that runs a single fixed executable.

    The agent can pass arguments and a timeout, but cannot control which
    binary runs. Non-zero exit codes are returned as data, not exceptions.

    Args:
        executable: The binary to run (e.g. "pytest", "uv").
        description: Human-readable description of what this tool does.
        arg_schema: Additional JSON Schema properties to merge into the
            parameter schema. For example, ``{"pattern": {"type": "string"}}``
            adds a ``pattern`` property.
        read_root: Working directory for the subprocess.
        timeout_ceiling: Maximum allowed timeout in seconds.
        preview_threshold: Byte threshold for stdout/stderr preview.
        preview_lines: Number of head/tail lines in previews.

    Returns:
        A Tool instance for running the executable.
    """
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {**_BASE_PROPERTIES, **arg_schema},
        "additionalProperties": False,
    }

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, schema)

        cmd_args: list[str] = arguments.get("args", [])
        requested_timeout: int = arguments.get("timeout", timeout_ceiling)
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

        result: dict[str, Any] = {
            "stdout": stdout_preview.content,
            "stderr": stderr_preview.content,
            "exit_code": process.returncode or 0,
            "timed_out": timed_out,
            "duration_ms": duration_ms,
        }
        return json.dumps(result)

    return Tool(
        name=executable,
        description=description,
        parameters=schema,
        execute=execute,
    )
