"""Native worker: standalone process that executes tools locally.

Connects to a brain over WebSocket, receives tool calls, executes
them against the local filesystem, and returns results.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Any

from orxtra.protocols import Tool, ToolCapability, ToolOutput
from orxtra.tool import (
    make_copy_tool,
    make_delete_tool,
    make_diff_tool,
    make_edit_tool,
    make_git_tool,
    make_glob_tool,
    make_grep_tool,
    make_list_dir_tool,
    make_mkdir_tool,
    make_move_tool,
    make_multi_edit_tool,
    make_read_tool,
    make_set_executable_tool,
    make_stat_tool,
    make_write_tool,
    run_subprocess,
)
from orxtra.worker._protocol import (
    ExecuteToolCall,
    Heartbeat,
    HeartbeatAck,
    ToolCallResult,
    WorkerRegistration,
)
from orxtra.write_safety import StaleWriteTracker, WriteQueue, with_transient_retry

_logger = logging.getLogger("orxtra.worker.native")

_RECONNECT_BASE_DELAY_S = 1.0
_RECONNECT_MAX_DELAY_S = 60.0
_RECONNECT_BACKOFF_FACTOR = 2.0

_DEFAULT_PREVIEW_THRESHOLD = 50_000
_DEFAULT_PREVIEW_LINES = 30
_DEFAULT_TIMEOUT_CEILING = 300

def _make_worker_exec_tool(
    executable: str,
    description: str,
    read_root: Path,
) -> Tool:
    """Build a simple exec-style tool for the worker.

    Uses run_subprocess directly. The tool schema matches the
    historic exec tool: ``args`` (optional list[str]) and
    ``timeout`` (optional int).
    """
    _ = description  # Kept for readability at call sites.

    async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
        cmd_args: list[str] = args.get("args", [])
        timeout: int | None = args.get("timeout")
        effective_timeout = min(
            timeout if timeout is not None else _DEFAULT_TIMEOUT_CEILING,
            _DEFAULT_TIMEOUT_CEILING,
        )
        return await run_subprocess(
            executable=executable,
            args=cmd_args,
            cwd=read_root,
            timeout=effective_timeout,
            arg_validation=True,
            preview_threshold=_DEFAULT_PREVIEW_THRESHOLD,
            preview_lines=_DEFAULT_PREVIEW_LINES,
        )

    return Tool(
        name=executable,
        description=f"Run {executable}",
        parameters={
            "type": "object",
            "properties": {
                "args": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Command-line arguments",
                },
                "timeout": {
                    "type": "integer",
                    "description": "Timeout in seconds",
                    "minimum": 1,
                },
            },
            "additionalProperties": False,
        },
        execute=execute,
        namespace="exec",
        tags=frozenset({"mutation"}),
    )


def build_worker_tools(
    root: Path,
    queue: WriteQueue,
    tracker: StaleWriteTracker,
    session_id: str,
) -> dict[str, Tool]:
    """Build the essential tool set for a worker.

    Constructs read, write, edit, grep, glob, stat, diff, list_dir,
    git, exec (pytest, uv), mkdir, move, copy, delete, multi_edit,
    and set_executable tools scoped to the given root.
    """
    write_scope = [root]
    tools: list[Tool] = [
        make_read_tool(
            read_root=root,
            preview_threshold=_DEFAULT_PREVIEW_THRESHOLD,
            preview_lines=_DEFAULT_PREVIEW_LINES,
            session_id=session_id,
        ),
        make_write_tool(
            read_root=root,
            write_scope=write_scope,
            queue=queue,
            tracker=tracker,
            session_id=session_id,
        ),
        make_edit_tool(
            read_root=root,
            write_scope=write_scope,
            queue=queue,
            tracker=tracker,
            session_id=session_id,
        ),
        make_multi_edit_tool(
            read_root=root,
            write_scope=write_scope,
            queue=queue,
            tracker=tracker,
            session_id=session_id,
        ),
        make_grep_tool(
            read_root=root,
            preview_threshold=_DEFAULT_PREVIEW_THRESHOLD,
            preview_lines=_DEFAULT_PREVIEW_LINES,
        ),
        make_glob_tool(read_root=root),
        make_stat_tool(read_root=root),
        make_diff_tool(read_root=root),
        make_list_dir_tool(read_root=root),
        make_mkdir_tool(read_root=root, write_scope=write_scope),
        make_move_tool(
            read_root=root,
            write_scope=write_scope,
            queue=queue,
            tracker=tracker,
            session_id=session_id,
        ),
        make_copy_tool(
            read_root=root,
            write_scope=write_scope,
            queue=queue,
            tracker=tracker,
            session_id=session_id,
        ),
        make_delete_tool(read_root=root, write_scope=write_scope),
        make_set_executable_tool(read_root=root, write_scope=write_scope),
        make_git_tool(
            read_root=root,
            allowed_subcommands=[
                "status", "log", "diff", "show", "blame",
                "branches", "changed_files", "commit",
            ],
        ),
        _make_worker_exec_tool("pytest", "Run pytest", root),
        _make_worker_exec_tool("uv", "Run uv", root),
    ]
    return {t.name: t for t in tools}


def _serialize_message(msg_type: str, model: Any) -> str:
    """Serialize a protocol message as a JSON envelope."""
    return json.dumps({
        "type": msg_type,
        "data": json.loads(model.model_dump_json()),
    })


class NativeWorker:
    """Standalone worker process that connects to a brain over WebSocket.

    Receives ExecuteToolCall messages, executes tools locally against
    the given root directory, and returns ToolCallResult messages.
    """

    def __init__(
        self,
        brain_url: str,
        root: Path,
        api_key: str,
        capabilities: list[ToolCapability] | None = None,
    ) -> None:
        self._brain_url = brain_url
        self._root = root.resolve()
        self._api_key = api_key
        self._capabilities = capabilities or []
        self._queue = WriteQueue()
        self._tracker = StaleWriteTracker()
        self._session_id = f"worker-{id(self)}"
        self._tools: dict[str, Tool] = {}
        self._running = False

    async def run(self) -> None:
        """Main loop: connect, register, process messages, reconnect on failure."""
        self._tools = build_worker_tools(
            root=self._root,
            queue=self._queue,
            tracker=self._tracker,
            session_id=self._session_id,
        )
        self._running = True
        delay = _RECONNECT_BASE_DELAY_S

        while self._running:
            try:
                await self._connect_and_run()
                # Clean exit -- don't reconnect.
                break
            except Exception:
                _logger.exception("Connection lost, reconnecting in %.1fs", delay)
                await asyncio.sleep(delay)
                delay = min(delay * _RECONNECT_BACKOFF_FACTOR, _RECONNECT_MAX_DELAY_S)

    async def _connect_and_run(self) -> None:
        """Connect to the brain, register, and enter the message loop."""
        import websockets

        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with websockets.connect(
            self._brain_url, additional_headers=headers,
        ) as ws:
            _logger.info("Connected to brain at %s", self._brain_url)

            # Send registration.
            reg = WorkerRegistration(
                root=str(self._root),
                capabilities=self._capabilities,
            )
            await ws.send(_serialize_message("worker_registration", reg))
            _logger.info(
                "Registered with root=%s, capabilities=%s",
                self._root,
                self._capabilities,
            )

            # Message loop.
            async for raw_message in ws:
                text = (
                    raw_message.decode("utf-8")
                    if isinstance(raw_message, bytes)
                    else raw_message
                )
                await self._handle_message(ws, text)

    async def _handle_message(self, ws: Any, raw: str) -> None:
        """Parse and dispatch a single message from the brain."""
        try:
            envelope: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            _logger.warning("Invalid JSON from brain: %s", raw[:100])
            return

        msg_type = envelope.get("type")
        data = envelope.get("data", {})

        if msg_type == "execute_tool_call":
            # Use model_validate_json for strict UUID handling:
            # wire data has UUIDs as strings, strict mode requires
            # UUID objects. model_validate_json coerces correctly.
            call = ExecuteToolCall.model_validate_json(json.dumps(data))
            result = await self._execute_tool(call)
            await ws.send(_serialize_message("tool_call_result", result))

        elif msg_type == "heartbeat":
            hb = Heartbeat.model_validate_json(json.dumps(data))
            ack = HeartbeatAck(timestamp=hb.timestamp)
            await ws.send(_serialize_message("heartbeat_ack", ack))

        else:
            _logger.warning("Unknown message type from brain: %s", msg_type)

    async def _execute_tool(self, call: ExecuteToolCall) -> ToolCallResult:
        """Execute a tool call locally and return the result."""
        tool = self._tools.get(call.tool_name)
        if tool is None:
            return ToolCallResult(
                call_id=call.call_id,
                output="",
                error=f"Unknown tool: {call.tool_name}",
            )

        start = time.monotonic()
        try:
            result: ToolOutput[Any] = await with_transient_retry(
                tool.execute, call.args,
            )
            end = time.monotonic()
            duration_ms = (end - start) * 1000

            # Track mutations from write tools.
            mutations: list[str] = []
            if "path" in call.args and tool.name in {
                "write", "edit", "multi_edit", "delete",
                "move", "copy", "mkdir", "set_executable",
            }:
                mutations.append(str(call.args["path"]))
            if "source" in call.args and tool.name in {"move", "copy"}:
                mutations.append(str(call.args["source"]))
            if "destination" in call.args and tool.name in {"move", "copy"}:
                mutations.append(str(call.args["destination"]))

            return ToolCallResult(
                call_id=call.call_id,
                output=result.text,
                data=result.data,
                mutations=mutations,
                duration_ms=duration_ms,
            )
        except Exception as exc:
            end = time.monotonic()
            duration_ms = (end - start) * 1000
            _logger.exception("Tool %s failed", call.tool_name)
            return ToolCallResult(
                call_id=call.call_id,
                output="",
                error=str(exc),
                duration_ms=duration_ms,
            )

    def stop(self) -> None:
        """Signal the worker to stop after the current message."""
        self._running = False
