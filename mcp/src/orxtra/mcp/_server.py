from __future__ import annotations

# ruff: noqa: ANN401, C901, PLR0911
import asyncio
import contextlib
import json
import sys
from collections.abc import Callable, Coroutine
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

from orxtra.mcp._tools import get_tool_definitions
from orxtra.services import (
    abort_run,
    dump_config,
    event_stream,
    fire_event,
    get_inbox_item,
    get_notepad,
    get_run,
    get_task_attempts,
    get_transcript,
    list_inbox,
    list_runs,
    list_tasks,
    pause_run,
    query_events,
    reject_inbox_item,
    respond_to_inbox,
    resume_run,
    search_transcript,
    show_pricing,
    skip_inbox_item,
    start_run_from_file,
)
from orxtra.trace import PgEventBus
from pydantic import BaseModel

_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


def _serialize(obj: Any) -> Any:
    if obj is None:
        return None
    if isinstance(obj, UUID):
        return str(obj)
    if isinstance(obj, Decimal):
        return str(obj)
    if isinstance(obj, datetime):
        return obj.isoformat()
    if isinstance(obj, Path):
        return str(obj)
    if isinstance(obj, BaseModel):
        return _serialize(obj.model_dump())
    if isinstance(obj, list):
        return [_serialize(item) for item in obj]
    if isinstance(obj, dict):
        return {k: _serialize(v) for k, v in obj.items()}
    return obj


def _jsonrpc_error(
    request_id: int | str | None, code: int, message: str
) -> dict[str, Any]:
    response: dict[str, Any] = {
        "jsonrpc": "2.0",
        "error": {"code": code, "message": message},
    }
    response["id"] = request_id
    return response


def _jsonrpc_result(
    request_id: int | str | None, result: Any
) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "result": result,
    }


ToolHandler = Callable[
    [dict[str, Any]], Coroutine[Any, Any, Any]
]


class MCPServer:
    def __init__(self, pool: Any) -> None:
        self._pool = pool
        self._handlers: dict[
            str,
            Callable[
                [dict[str, Any]],
                Coroutine[Any, Any, dict[str, Any]],
            ],
        ] = {
            "initialize": self._handle_initialize,
            "tools/list": self._handle_tools_list,
            "tools/call": self._handle_tools_call,
        }
        self._tool_dispatch: dict[str, ToolHandler] = self._build_dispatch()

    def _build_dispatch(self) -> dict[str, ToolHandler]:
        pool = self._pool

        async def _start_run(p: dict[str, Any]) -> Any:
            return await start_run_from_file(
                pool, intent=str(p["intent"]), config_path=Path(p["config_path"])
            )

        async def _list_runs(_p: dict[str, Any]) -> Any:
            return await list_runs(pool)

        async def _get_run(p: dict[str, Any]) -> Any:
            return await get_run(pool, run_id=UUID(p["run_id"]))

        async def _abort_run(p: dict[str, Any]) -> Any:
            return await abort_run(pool, run_id=UUID(p["run_id"]))

        async def _pause_run(p: dict[str, Any]) -> Any:
            return await pause_run(pool, run_id=UUID(p["run_id"]))

        async def _resume_run(p: dict[str, Any]) -> Any:
            return await resume_run(pool, run_id=UUID(p["run_id"]))

        async def _list_inbox(p: dict[str, Any]) -> Any:
            return await list_inbox(
                pool, run_id=UUID(p["run_id"]), status=p.get("status")
            )

        async def _get_inbox_item(p: dict[str, Any]) -> Any:
            return await get_inbox_item(pool, item_id=UUID(p["item_id"]))

        async def _respond_to_inbox(p: dict[str, Any]) -> Any:
            return await respond_to_inbox(
                pool, item_id=UUID(p["item_id"]), answer=str(p["answer"])
            )

        async def _skip_inbox_item(p: dict[str, Any]) -> Any:
            return await skip_inbox_item(pool, item_id=UUID(p["item_id"]))

        async def _reject_inbox_item(p: dict[str, Any]) -> Any:
            return await reject_inbox_item(
                pool, item_id=UUID(p["item_id"]), reason=str(p["reason"])
            )

        async def _query_events(p: dict[str, Any]) -> Any:
            since: datetime | None = None
            if "since" in p:
                since = datetime.fromisoformat(p["since"])
            return await query_events(
                pool,
                run_id=UUID(p["run_id"]),
                event_type=p.get("event_type"),
                since=since,
                limit=int(p.get("limit", 100)),
            )

        async def _get_transcript(p: dict[str, Any]) -> Any:
            return await get_transcript(pool, session_id=UUID(p["session_id"]))

        async def _search_transcript(p: dict[str, Any]) -> Any:
            return await search_transcript(
                pool, session_id=UUID(p["session_id"]), query=str(p["query"])
            )

        async def _list_tasks(p: dict[str, Any]) -> Any:
            return await list_tasks(pool, run_id=UUID(p["run_id"]))

        async def _get_task_attempts(p: dict[str, Any]) -> Any:
            return await get_task_attempts(pool, task_id=UUID(p["task_id"]))

        async def _get_notepad(p: dict[str, Any]) -> Any:
            return await get_notepad(pool, run_id=UUID(p["run_id"]))

        async def _fire_event(p: dict[str, Any]) -> Any:
            return await fire_event(
                pool,
                run_id=UUID(p["run_id"]),
                event_name=str(p["event_name"]),
                payload=p.get("payload"),
            )

        async def _show_config(p: dict[str, Any]) -> Any:
            return await dump_config(pool, run_id=UUID(p["run_id"]))

        async def _show_pricing(_p: dict[str, Any]) -> Any:
            return await show_pricing()

        return {
            "start_run": _start_run,
            "list_runs": _list_runs,
            "get_run": _get_run,
            "abort_run": _abort_run,
            "pause_run": _pause_run,
            "resume_run": _resume_run,
            "list_inbox": _list_inbox,
            "get_inbox_item": _get_inbox_item,
            "respond_to_inbox": _respond_to_inbox,
            "skip_inbox_item": _skip_inbox_item,
            "reject_inbox_item": _reject_inbox_item,
            "query_events": _query_events,
            "get_transcript": _get_transcript,
            "search_transcript": _search_transcript,
            "list_tasks": _list_tasks,
            "get_task_attempts": _get_task_attempts,
            "get_notepad": _get_notepad,
            "fire_event": _fire_event,
            "show_config": _show_config,
            "show_pricing": _show_pricing,
        }

    async def handle_request(self, request: dict[str, Any]) -> dict[str, Any]:
        request_id: int | str | None = request.get("id")

        if request.get("jsonrpc") != "2.0":
            return _jsonrpc_error(
                request_id, _INVALID_REQUEST, "Invalid JSON-RPC version"
            )

        method: Any = request.get("method")
        if not isinstance(method, str):
            return _jsonrpc_error(
                request_id, _INVALID_REQUEST, "Missing or invalid method"
            )

        handler = self._handlers.get(method)
        if handler is None:
            msg = f"Unknown method: {method}"
            return _jsonrpc_error(request_id, _METHOD_NOT_FOUND, msg)

        try:
            result = await handler(request)
        except Exception as exc:  # noqa: BLE001
            return _jsonrpc_error(request_id, _INTERNAL_ERROR, str(exc))

        return _jsonrpc_result(request_id, result)

    async def _handle_initialize(self, _request: dict[str, Any]) -> dict[str, Any]:
        return {
            "protocolVersion": "2024-11-05",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "orxtra-mcp", "version": "0.0.0"},
        }

    async def _handle_tools_list(self, _request: dict[str, Any]) -> dict[str, Any]:
        return {"tools": get_tool_definitions()}

    async def _handle_tools_call(self, request: dict[str, Any]) -> dict[str, Any]:
        params: Any = request.get("params")
        if not isinstance(params, dict):
            msg = "Missing params"
            raise TypeError(msg)

        tool_name: Any = params.get("name")
        if not isinstance(tool_name, str):
            msg = "Missing tool name"
            raise TypeError(msg)

        arguments: dict[str, Any] = params.get("arguments") or {}

        dispatch_fn = self._tool_dispatch.get(tool_name)
        if dispatch_fn is None:
            msg = f"Unknown tool: {tool_name}"
            raise ValueError(msg)

        result = await dispatch_fn(arguments)
        serialized = _serialize(result)
        text = json.dumps(serialized)

        return {
            "content": [{"type": "text", "text": text}],
        }

    async def _start_event_listener(
        self, writer: asyncio.StreamWriter,
    ) -> asyncio.Task[Any]:
        """Start a background task that streams events via services event_stream
        and forwards them as JSON-RPC notifications."""

        async def _listen() -> None:
            while True:
                bus = PgEventBus(self._pool)
                try:
                    async for event in event_stream(
                        bus, channel="orxtra_events",
                    ):
                        notification = {
                            "jsonrpc": "2.0",
                            "method": "notifications/event",
                            "params": event,
                        }
                        writer.write(
                            (json.dumps(notification) + "\n").encode(),
                        )
                        await writer.drain()
                except Exception:  # noqa: BLE001
                    # Connection dropped or bus error - clean up and retry
                    with contextlib.suppress(Exception):
                        await bus.close()
                    await asyncio.sleep(1)

        return asyncio.create_task(_listen())

    async def run_stdio(self) -> None:
        loop = asyncio.get_running_loop()
        reader = asyncio.StreamReader()
        protocol = asyncio.StreamReaderProtocol(reader)
        await loop.connect_read_pipe(lambda: protocol, sys.stdin)

        transport_out, _ = await loop.connect_write_pipe(
            asyncio.streams.FlowControlMixin, sys.stdout
        )
        writer = asyncio.StreamWriter(
            transport_out, protocol, reader, loop
        )

        # Start event stream listener if pool is available
        event_listener_task: asyncio.Task[Any] | None = None
        if self._pool is not None:
            event_listener_task = await self._start_event_listener(writer)

        while True:
            line = await reader.readline()
            if not line:
                break

            try:
                request = json.loads(line)
            except json.JSONDecodeError:
                response = _jsonrpc_error(None, _PARSE_ERROR, "Parse error")
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
                continue

            if not isinstance(request, dict):
                response = _jsonrpc_error(
                    None, _INVALID_REQUEST, "Request must be an object"
                )
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()
                continue

            response = await self.handle_request(request)

            if request.get("id") is not None:
                writer.write((json.dumps(response) + "\n").encode())
                await writer.drain()

        if event_listener_task is not None:
            event_listener_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await event_listener_task
