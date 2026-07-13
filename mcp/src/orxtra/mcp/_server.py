from __future__ import annotations

import dataclasses
import json
from collections.abc import Callable
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.protocols import Capability
from orxtra.services import DispatchContext, dispatch, get_capabilities
from pydantic import BaseModel

# MCP SDK imports are deferred to function bodies to avoid a name collision
# with the orxtra workspace member directory mcp/ during pytest's
# --import-mode=importlib conftest discovery.
if TYPE_CHECKING:
    from orxtra.protocols import AuthContext

    from mcp.server.fastmcp import Context, FastMCP
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


def _auth_context_from_ctx(ctx: Context[Any, Any, Any]) -> AuthContext | None:
    """Extract the per-request AuthContext from the MCP request scope.

    The api compositor's auth middleware sets
    ``scope["state"]["auth_context"]`` for authenticated HTTP requests; the
    key is absent when no authenticator is configured (explicit open mode),
    yielding ``None``. This is the single seam through which per-request
    identity enters dispatch for both tools and resources.
    """
    request = ctx.request_context.request
    if request is None:
        return None
    state = request.scope.get("state", {})
    auth_context: AuthContext | None = state.get("auth_context")
    return auth_context


def _build_fastmcp(
    dispatch_context: DispatchContext,
) -> FastMCP:
    """Create a FastMCP instance with all tools and resources registered."""
    from mcp.server.fastmcp import Context as _Context
    from mcp.server.fastmcp import FastMCP as _FastMCP

    mcp_app = _FastMCP("orxtra-mcp")

    # Register tools from capabilities
    for cap in _mcp_capabilities():
        annotations = _annotations_for_capability(cap)
        _register_tool(mcp_app, cap, dispatch_context, annotations, _Context)

    # Register resources
    _register_resources(mcp_app, dispatch_context, _Context)

    return mcp_app


def _register_tool(
    mcp_app: FastMCP,
    cap: Capability,
    context: DispatchContext,
    annotations: ToolAnnotations,
    context_cls: type[Context[Any, Any, Any]],
) -> None:
    """Register a single capability as an MCP tool on the FastMCP instance.

    The handler receives a per-request ``Context`` (injected by the SDK and
    excluded from the tool's input schema) and dispatches against a per-request
    copy of the construction-time context carrying the caller's identity.
    """
    cap_name = cap.name

    async def handler(ctx: Context[Any, Any, Any], **kwargs: Any) -> str:
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, cap_name, kwargs)
        return json.dumps(_serialize(result))

    # Give the handler a unique __name__ so FastMCP can distinguish them.
    handler.__name__ = cap_name
    handler.__qualname__ = f"_tool_{cap_name}"
    # ``from __future__ import annotations`` stringifies the ``ctx`` annotation,
    # and the MCP SDK is imported lazily (not a module global), so the SDK's
    # get_type_hints-based Context detection cannot resolve it. Bind the real
    # class directly so the SDK recognizes the injection parameter.
    handler.__annotations__["ctx"] = context_cls

    mcp_app.add_tool(
        handler,
        name=cap.name,
        description=cap.description,
        annotations=annotations,
    )


def _register_resources(
    mcp_app: FastMCP,
    context: DispatchContext,
    context_cls: type[Context[Any, Any, Any]],
) -> None:
    """Register MCP resources backed by services dispatch.

    Static resources obtain the per-request ``Context`` via
    ``mcp_app.get_context()`` (the SDK does not inject a parameter into
    zero-argument resource functions), while parameterized resource templates
    receive it as an injected ``Context`` parameter. Both funnel through
    ``_auth_context_from_ctx`` so identity flows into dispatch uniformly.
    """

    async def pricing_resource() -> str:
        ctx = mcp_app.get_context()
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, "show_pricing", {})
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
        ctx = mcp_app.get_context()
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, "list_runs", {})
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

    # Parameterized resources (registered as resource templates). The ``ctx``
    # parameter is injected per request by the SDK and excluded from the
    # template's parameters; see _register_tool for the annotation-binding note.
    async def run_report_resource(run_id: str, ctx: Context[Any, Any, Any]) -> str:
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, "get_run", {"run_id": run_id})
        return json.dumps(_serialize(result))

    run_report_resource.__annotations__["ctx"] = context_cls
    mcp_app.resource(
        "orxtra://runs/{run_id}",
        name="run_report",
        description="Single run report",
        mime_type="application/json",
    )(run_report_resource)

    async def run_tasks_resource(run_id: str, ctx: Context[Any, Any, Any]) -> str:
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, "list_tasks", {"run_id": run_id})
        return json.dumps(_serialize(result))

    run_tasks_resource.__annotations__["ctx"] = context_cls
    mcp_app.resource(
        "orxtra://runs/{run_id}/tasks",
        name="run_tasks",
        description="Tasks for a run",
        mime_type="application/json",
    )(run_tasks_resource)

    async def run_inbox_resource(run_id: str, ctx: Context[Any, Any, Any]) -> str:
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, "list_inbox", {"run_id": run_id})
        return json.dumps(_serialize(result))

    run_inbox_resource.__annotations__["ctx"] = context_cls
    mcp_app.resource(
        "orxtra://runs/{run_id}/inbox",
        name="run_inbox",
        description="Inbox items for a run",
        mime_type="application/json",
    )(run_inbox_resource)

    async def run_notepad_resource(run_id: str, ctx: Context[Any, Any, Any]) -> str:
        auth_context = _auth_context_from_ctx(ctx)
        request_context = dataclasses.replace(context, auth_context=auth_context)
        result = await dispatch(request_context, "get_notepad", {"run_id": run_id})
        return json.dumps(_serialize(result))

    run_notepad_resource.__annotations__["ctx"] = context_cls
    mcp_app.resource(
        "orxtra://runs/{run_id}/notepad",
        name="run_notepad",
        description="Notepad entries for a run",
        mime_type="application/json",
    )(run_notepad_resource)


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


class MCPServer:
    def __init__(
        self,
        pool: Any,
        dispatch_context: DispatchContext | None = None,
    ) -> None:
        self._pool = pool
        self._dispatch_context = dispatch_context or DispatchContext(pool=pool)
        self._fastmcp = _build_fastmcp(self._dispatch_context)

    @property
    def fastmcp(self) -> FastMCP:
        """The underlying FastMCP instance."""
        return self._fastmcp
