from __future__ import annotations

import asyncio
import contextlib
import json
import sys
from collections.abc import Callable, Coroutine
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.protocols import Capability, EventBus
from orxtra.services import DispatchContext, dispatch, event_stream, get_capabilities
from pydantic import BaseModel

# MCP SDK imports are deferred to function bodies to avoid a name collision
# with the orxtra workspace member directory mcp/ during pytest's
# --import-mode=importlib conftest discovery.
if TYPE_CHECKING:
    from mcp.server.fastmcp import FastMCP
    from mcp.types import ToolAnnotations

# Capabilities exposed as MCP tools. Validation tools are excluded
# because they require local filesystem access that MCP clients don't have.
_MCP_EXCLUDED_NAMESPACES: frozenset[str] = frozenset({
    "validate",
    "dispatch",
})


def _annotations_for_capability(cap: Capability) -> ToolAnnotations:
    """Derive MCP ToolAnnotations from capability tags."""
    from mcp.types import ToolAnnotations as _ToolAnnotations

    if "readonly" in cap.tags:
        return _ToolAnnotations(readOnlyHint=True, destructiveHint=False)
    if "mutating" in cap.tags:
        return _ToolAnnotations(destructiveHint=True, readOnlyHint=False)
    return _ToolAnnotations()


def _mcp_capabilities() -> list[Capability]:
    """Return capabilities visible to MCP (excluding internal namespaces)."""
    return [
        c for c in get_capabilities()
        if c.namespace not in _MCP_EXCLUDED_NAMESPACES
    ]


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


def _project_tools(capabilities: list[Capability]) -> list[dict[str, object]]:
    """Project capabilities into MCP tool definition dicts.

    Kept for backward compatibility with get_tool_definitions().
    """
    tools: list[dict[str, object]] = []
    for cap in capabilities:
        schema = cap.params_model.model_json_schema()
        input_schema = _simplify_schema(schema)
        tools.append({
            "name": cap.name,
            "description": cap.description,
            "inputSchema": input_schema,
        })
    return tools


def _simplify_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Convert a pydantic JSON schema to the MCP tool inputSchema format."""
    result: dict[str, Any] = {"type": "object"}
    props = schema.get("properties", {})
    required = list(schema.get("required", []))
    simplified_props: dict[str, Any] = {}

    for name, prop in props.items():
        simplified: dict[str, Any] = {}

        if "anyOf" in prop:
            non_null = [t for t in prop["anyOf"] if t.get("type") != "null"]
            if len(non_null) == 1:
                simplified.update(
                    {k: v for k, v in non_null[0].items() if k != "title"},
                )
        else:
            simplified.update(
                {k: v for k, v in prop.items() if k not in ("title", "description")},
            )

        if "format" in prop and "format" not in simplified:
            simplified["format"] = prop["format"]

        simplified_props[name] = simplified

    result["properties"] = simplified_props
    result["required"] = required
    return result


def get_tool_definitions() -> list[dict[str, object]]:
    """Return MCP tool definitions projected from capabilities."""
    return _project_tools(_mcp_capabilities())


def _build_fastmcp(
    dispatch_context: DispatchContext,
) -> FastMCP:
    """Create a FastMCP instance with all tools and resources registered."""
    from mcp.server.fastmcp import FastMCP as _FastMCP

    mcp_app = _FastMCP("orxtra-mcp")

    # Register tools from capabilities
    for cap in _mcp_capabilities():
        annotations = _annotations_for_capability(cap)
        _register_tool(mcp_app, cap, dispatch_context, annotations)

    # Register resources
    _register_resources(mcp_app, dispatch_context)

    return mcp_app


def _register_tool(
    mcp_app: FastMCP,
    cap: Capability,
    context: DispatchContext,
    annotations: ToolAnnotations,
) -> None:
    """Register a single capability as an MCP tool on the FastMCP instance."""
    cap_name = cap.name

    async def handler(**kwargs: Any) -> str:
        result = await dispatch(context, cap_name, kwargs)
        serialized = _serialize(result)
        return json.dumps(serialized)

    # Give the handler a unique __name__ so FastMCP can distinguish them
    handler.__name__ = cap_name
    handler.__qualname__ = f"_tool_{cap_name}"

    mcp_app.add_tool(
        handler,
        name=cap.name,
        description=cap.description,
        annotations=annotations,
    )


def _register_resources(
    mcp_app: FastMCP,
    context: DispatchContext,
) -> None:
    """Register MCP resources backed by services dispatch."""

    async def pricing_resource() -> str:
        result = await dispatch(context, "show_pricing", {})
        return json.dumps(_serialize(result))

    pricing_resource.__name__ = "pricing_resource"

    mcp_app.add_resource(
        _make_function_resource(
            "orxtra://pricing",
            pricing_resource,
            name="pricing",
            description="Model pricing table",
            mime_type="application/json",
        ),
    )

    async def list_runs_resource() -> str:
        result = await dispatch(context, "list_runs", {})
        return json.dumps(_serialize(result))

    list_runs_resource.__name__ = "list_runs_resource"

    mcp_app.add_resource(
        _make_function_resource(
            "orxtra://runs",
            list_runs_resource,
            name="runs",
            description="List of all runs",
            mime_type="application/json",
        ),
    )

    # Parameterized resources (registered as resource templates)
    @mcp_app.resource(
        "orxtra://runs/{run_id}",
        name="run_report",
        description="Single run report",
        mime_type="application/json",
    )
    async def run_report_resource(run_id: str) -> str:
        result = await dispatch(context, "get_run", {"run_id": run_id})
        return json.dumps(_serialize(result))

    @mcp_app.resource(
        "orxtra://runs/{run_id}/tasks",
        name="run_tasks",
        description="Tasks for a run",
        mime_type="application/json",
    )
    async def run_tasks_resource(run_id: str) -> str:
        result = await dispatch(context, "list_tasks", {"run_id": run_id})
        return json.dumps(_serialize(result))

    @mcp_app.resource(
        "orxtra://runs/{run_id}/inbox",
        name="run_inbox",
        description="Inbox items for a run",
        mime_type="application/json",
    )
    async def run_inbox_resource(run_id: str) -> str:
        result = await dispatch(context, "list_inbox", {"run_id": run_id})
        return json.dumps(_serialize(result))

    @mcp_app.resource(
        "orxtra://runs/{run_id}/notepad",
        name="run_notepad",
        description="Notepad entries for a run",
        mime_type="application/json",
    )
    async def run_notepad_resource(run_id: str) -> str:
        result = await dispatch(context, "get_notepad", {"run_id": run_id})
        return json.dumps(_serialize(result))


def _make_function_resource(
    uri: str,
    fn: Callable[[], Any],
    *,
    name: str,
    description: str,
    mime_type: str,
) -> Any:
    """Create a FunctionResource from a callable."""
    from mcp.server.fastmcp.resources.types import FunctionResource
    return FunctionResource.from_function(
        fn,
        uri=uri,
        name=name,
        description=description,
        mime_type=mime_type,
    )


ToolHandler = Callable[
    [dict[str, Any]], Coroutine[Any, Any, Any]
]


class MCPServer:
    def __init__(
        self,
        pool: Any,
        event_bus: EventBus | None = None,
        dispatch_context: DispatchContext | None = None,
    ) -> None:
        self._pool = pool
        self._event_bus = event_bus
        self._dispatch_context = dispatch_context or DispatchContext(pool=pool)
        self._fastmcp = _build_fastmcp(self._dispatch_context)
        self._tool_names: set[str] = {
            c.name for c in get_capabilities()
            if c.namespace not in _MCP_EXCLUDED_NAMESPACES
        }
        # Legacy JSON-RPC handlers for stdio mode and backward compat
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

    @property
    def fastmcp(self) -> FastMCP:
        """The underlying FastMCP instance."""
        return self._fastmcp

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

        if tool_name not in self._tool_names:
            msg = f"Unknown tool: {tool_name}"
            raise ValueError(msg)

        arguments: dict[str, Any] = params.get("arguments") or {}

        result = await dispatch(self._dispatch_context, tool_name, arguments)
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
                try:
                    assert self._event_bus is not None  # noqa: S101
                    async for event in event_stream(
                        self._event_bus, channel="orxtra_events",
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
                    # Connection dropped or bus error - retry after delay
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

        # Start event stream listener if event bus is available
        event_listener_task: asyncio.Task[Any] | None = None
        if self._event_bus is not None:
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


# JSON-RPC error codes
_PARSE_ERROR = -32700
_INVALID_REQUEST = -32600
_METHOD_NOT_FOUND = -32601
_INVALID_PARAMS = -32602
_INTERNAL_ERROR = -32603


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
