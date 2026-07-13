"""Tests for FastMCP SDK integration.

Covers tool registration with annotations, resources, streamable HTTP
factory, and the McpNotificationSink.
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from mcp.server.lowlevel.server import request_ctx
from mcp.shared.context import RequestContext
from orxtra.mcp._server import (
    MCPServer,
    _annotations_for_capability,
    _mcp_capabilities,
)
from orxtra.protocols import AuthContext, Capability, TrustTier
from orxtra.services import DispatchContext
from pydantic import BaseModel
from starlette.requests import Request

# Sentinel marking "no state key in the request scope" (open mode) for the
# per-request identity tests, distinct from an explicit None auth_context.
_ABSENT = object()

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def dispatch_context() -> DispatchContext:
    return DispatchContext(pool=AsyncMock())


@pytest.fixture
def server(dispatch_context: DispatchContext) -> MCPServer:
    return MCPServer(
        pool=dispatch_context.pool,
        dispatch_context=dispatch_context,
    )


# ------------------------------------------------------------------
# 4.1 -- FastMCP tool registration
# ------------------------------------------------------------------


def test_fastmcp_instance_created(server: MCPServer) -> None:
    """MCPServer creates a FastMCP instance internally."""
    assert server.fastmcp is not None
    assert server.fastmcp.name == "orxtra-mcp"


async def test_fastmcp_tools_registered(server: MCPServer) -> None:
    """All MCP-visible capabilities are registered as FastMCP tools."""
    tools = await server.fastmcp.list_tools()
    tool_names = {t.name for t in tools}
    cap_names = {c.name for c in _mcp_capabilities()}
    assert tool_names == cap_names


async def test_fastmcp_tool_count(server: MCPServer) -> None:
    """FastMCP has the same number of tools as the legacy get_tool_definitions."""
    tools = await server.fastmcp.list_tools()
    assert len(tools) == 24


async def test_fastmcp_tool_descriptions(server: MCPServer) -> None:
    """Tool descriptions match capability descriptions."""
    tools = await server.fastmcp.list_tools()
    caps = {c.name: c for c in _mcp_capabilities()}
    for tool in tools:
        assert tool.name in caps
        assert tool.description == caps[tool.name].description


# ------------------------------------------------------------------
# 4.2 -- Tool annotations
# ------------------------------------------------------------------


def test_readonly_annotation() -> None:
    """Capabilities with 'readonly' tag get readOnlyHint=True."""
    cap = Capability(
        name="test_readonly",
        namespace="test",
        description="test",
        params_model=BaseModel,
        result_model=None,
        tags=frozenset({"readonly"}),
        category="test",
        required_scope="test:read",
        injects=frozenset(),
    )
    ann = _annotations_for_capability(cap)
    assert ann.readOnlyHint is True
    assert ann.destructiveHint is False


def test_mutating_annotation() -> None:
    """Capabilities with 'mutating' tag get destructiveHint=True."""
    cap = Capability(
        name="test_mutating",
        namespace="test",
        description="test",
        params_model=BaseModel,
        result_model=None,
        tags=frozenset({"mutating"}),
        category="test",
        required_scope="test:manage",
        injects=frozenset(),
    )
    ann = _annotations_for_capability(cap)
    assert ann.destructiveHint is True
    assert ann.readOnlyHint is False


def test_no_tag_annotation() -> None:
    """Capabilities with no recognized tags get default annotations."""
    cap = Capability(
        name="test_none",
        namespace="test",
        description="test",
        params_model=BaseModel,
        result_model=None,
        tags=frozenset(),
        category="test",
        required_scope="test:read",
        injects=frozenset(),
    )
    ann = _annotations_for_capability(cap)
    assert ann.readOnlyHint is None
    assert ann.destructiveHint is None


async def test_fastmcp_tools_have_annotations(server: MCPServer) -> None:
    """Every registered tool has ToolAnnotations set."""
    tools = await server.fastmcp.list_tools()
    caps = {c.name: c for c in _mcp_capabilities()}
    for tool in tools:
        cap = caps[tool.name]
        expected = _annotations_for_capability(cap)
        assert tool.annotations is not None
        assert tool.annotations.readOnlyHint == expected.readOnlyHint
        assert tool.annotations.destructiveHint == expected.destructiveHint


async def test_readonly_tools_annotated_correctly(server: MCPServer) -> None:
    """Specific known readonly tools have readOnlyHint=True."""
    tools = await server.fastmcp.list_tools()
    readonly_names = {"list_runs", "get_run", "list_inbox", "show_pricing"}
    for tool in tools:
        if tool.name in readonly_names:
            assert tool.annotations is not None
            assert tool.annotations.readOnlyHint is True
            assert tool.annotations.destructiveHint is False


async def test_mutating_tools_annotated_correctly(server: MCPServer) -> None:
    """Specific known mutating tools have destructiveHint=True."""
    tools = await server.fastmcp.list_tools()
    mutating_names = {"start_run", "abort_run", "pause_run", "fire_event"}
    for tool in tools:
        if tool.name in mutating_names:
            assert tool.annotations is not None
            assert tool.annotations.destructiveHint is True
            assert tool.annotations.readOnlyHint is False


# ------------------------------------------------------------------
# 4.3 -- MCP Resources
# ------------------------------------------------------------------


async def test_static_resources_registered(server: MCPServer) -> None:
    """Static resources (pricing, runs) are registered."""
    resources = await server.fastmcp.list_resources()
    resource_uris = {str(r.uri) for r in resources}
    assert "orxtra://pricing" in resource_uris
    assert "orxtra://runs" in resource_uris


async def test_resource_templates_registered(server: MCPServer) -> None:
    """Parameterized resources are registered as templates."""
    templates = await server.fastmcp.list_resource_templates()
    template_uris = {t.uriTemplate for t in templates}
    assert "orxtra://runs/{run_id}" in template_uris
    assert "orxtra://runs/{run_id}/tasks" in template_uris
    assert "orxtra://runs/{run_id}/inbox" in template_uris
    assert "orxtra://runs/{run_id}/notepad" in template_uris


async def test_resource_template_count(server: MCPServer) -> None:
    """Exactly 4 resource templates are registered."""
    templates = await server.fastmcp.list_resource_templates()
    assert len(templates) == 4


async def test_static_resource_count(server: MCPServer) -> None:
    """Exactly 2 static resources are registered."""
    resources = await server.fastmcp.list_resources()
    assert len(resources) == 2


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_pricing_resource_reads(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """Reading the pricing resource dispatches to show_pricing."""
    mock_dispatch.return_value = {"gpt-4": {"input": "0.01"}}
    with _request_scope(auth_context=_ABSENT):
        contents = await server.fastmcp.read_resource("orxtra://pricing")
    assert len(contents) == 1
    parsed = json.loads(contents[0].content)
    assert parsed == {"gpt-4": {"input": "0.01"}}
    # Verify dispatch was called for show_pricing
    calls = [c for c in mock_dispatch.call_args_list if c[0][1] == "show_pricing"]
    assert len(calls) >= 1


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_runs_resource_reads(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """Reading the runs resource dispatches to list_runs."""
    mock_dispatch.return_value = []
    with _request_scope(auth_context=_ABSENT):
        contents = await server.fastmcp.read_resource("orxtra://runs")
    assert len(contents) == 1
    parsed = json.loads(contents[0].content)
    assert parsed == []
    calls = [c for c in mock_dispatch.call_args_list if c[0][1] == "list_runs"]
    assert len(calls) >= 1


# ------------------------------------------------------------------
# 4.4 -- Streamable HTTP factory
# ------------------------------------------------------------------


def test_create_app_returns_asgi(dispatch_context: DispatchContext) -> None:
    """create_app returns an ASGI-compatible app."""
    from orxtra.mcp._http import create_app
    app = create_app(dispatch_context)
    # ASGI apps must be callable with (scope, receive, send)
    assert callable(app)


def test_streamable_http_app_from_server(server: MCPServer) -> None:
    """FastMCP.streamable_http_app() returns a Starlette app."""
    from starlette.applications import Starlette
    app = server.fastmcp.streamable_http_app()
    assert isinstance(app, Starlette)


# ------------------------------------------------------------------
# 4.5 -- McpNotificationSink
# ------------------------------------------------------------------


def test_notification_sink_implements_protocol() -> None:
    """McpNotificationSink satisfies EventSink[OverseerEvent]."""
    from orxtra.mcp._notification_sink import McpNotificationSink
    from orxtra.protocols import EventSink

    sink = McpNotificationSink(mcp_app=AsyncMock())
    assert isinstance(sink, EventSink)


async def test_notification_sink_on_event_run_started() -> None:
    """RunStarted event triggers resource update notification."""
    from orxtra.mcp._notification_sink import McpNotificationSink
    from orxtra.protocols._types._events import RunStarted

    AsyncMock()
    mock_manager = AsyncMock()
    mock_manager._session_map = {}

    mock_app = AsyncMock()
    mock_app.session_manager = mock_manager

    sink = McpNotificationSink(mcp_app=mock_app)
    event = RunStarted(intent="test", config_snapshot={})

    # No sessions connected -- should not raise
    await sink.on_event(event)


async def test_notification_sink_on_event_with_session() -> None:
    """When sessions exist, resource update notifications are sent."""
    from unittest.mock import MagicMock

    from orxtra.mcp._notification_sink import McpNotificationSink
    from orxtra.protocols._types._events import RunStarted

    mock_session = AsyncMock()

    # Simulate a session in the session manager
    session_entry = MagicMock()
    session_entry.session = mock_session

    mock_manager = MagicMock()
    mock_manager._session_map = {"session-1": session_entry}

    mock_app = MagicMock()
    mock_app.session_manager = mock_manager

    sink = McpNotificationSink(mcp_app=mock_app)
    event = RunStarted(intent="test", config_snapshot={})

    await sink.on_event(event)

    mock_session.send_resource_updated.assert_awaited_once()


async def test_notification_sink_inbox_event() -> None:
    """InboxAnswered event triggers notification."""
    from unittest.mock import MagicMock
    from uuid import uuid4

    from orxtra.mcp._notification_sink import McpNotificationSink
    from orxtra.protocols._types._events import InboxAnswered

    mock_session = AsyncMock()

    session_entry = MagicMock()
    session_entry.session = mock_session

    mock_manager = MagicMock()
    mock_manager._session_map = {"session-1": session_entry}

    mock_app = MagicMock()
    mock_app.session_manager = mock_manager

    sink = McpNotificationSink(mcp_app=mock_app)
    event = InboxAnswered(
        item_id=uuid4(),
        assumed_option="yes",
        actual_answer="no",
        contradicts=True,
    )

    await sink.on_event(event)

    mock_session.send_resource_updated.assert_awaited_once()


async def test_notification_sink_task_failed_event() -> None:
    """TaskFailed event triggers notification."""
    from unittest.mock import MagicMock
    from uuid import uuid4

    from orxtra.mcp._notification_sink import McpNotificationSink
    from orxtra.protocols._types._events import TaskFailed
    from orxtra.protocols._types._task import EscalationPayload, TaskContext

    mock_session = AsyncMock()

    session_entry = MagicMock()
    session_entry.session = mock_session

    mock_manager = MagicMock()
    mock_manager._session_map = {"session-1": session_entry}

    mock_app = MagicMock()
    mock_app.session_manager = mock_manager

    sink = McpNotificationSink(mcp_app=mock_app)
    task_id = uuid4()
    run_id = uuid4()
    context = TaskContext(
        variables={},
        run_id=run_id,
        task_name="test_task",
        task_id=task_id,
        attempt=1,
        prior_attempts=None,
        notepad_content="",
        parent_task_id=None,
        nesting_depth=0,
    )
    event = TaskFailed(
        task_id=task_id,
        task_name="test_task",
        payload=EscalationPayload(
            task_name="test_task",
            task_id=task_id,
            agent_name=None,
            attempts=1,
            failed_checks=[],
            agent_summary="Task failed",
            context=context,
        ),
    )

    await sink.on_event(event)

    mock_session.send_resource_updated.assert_awaited_once()


async def test_notification_sink_handles_send_failure() -> None:
    """If sending notification fails, it's logged and not raised."""
    from unittest.mock import MagicMock

    from orxtra.mcp._notification_sink import McpNotificationSink
    from orxtra.protocols._types._events import RunStarted

    mock_session = AsyncMock()
    mock_session.send_resource_updated.side_effect = RuntimeError("disconnected")

    session_entry = MagicMock()
    session_entry.session = mock_session

    mock_manager = MagicMock()
    mock_manager._session_map = {"session-1": session_entry}

    mock_app = MagicMock()
    mock_app.session_manager = mock_manager

    sink = McpNotificationSink(mcp_app=mock_app)
    event = RunStarted(intent="test", config_snapshot={})

    # Should not raise
    await sink.on_event(event)


# ------------------------------------------------------------------
# 4.6 -- Per-request identity (auth_context flows from the request scope)
# ------------------------------------------------------------------


def _make_auth_context() -> AuthContext:
    """Build a distinct sentinel AuthContext for identity-flow assertions."""
    now = datetime(2026, 1, 1, tzinfo=UTC)
    return AuthContext(
        id=uuid4(),
        consumer_id=uuid4(),
        scopes=frozenset({"runs:read"}),
        trust_tier=TrustTier.VERIFIED,
        authenticated_via="test",
        issued_at=now,
        expires_at=None,
    )


@contextlib.contextmanager
def _request_scope(*, auth_context: Any) -> Iterator[None]:
    """Drive the FastMCP app with a Starlette request scope.

    When ``auth_context`` is the ``_ABSENT`` sentinel, no ``state`` key is
    placed in the scope (open mode); otherwise ``state.auth_context`` carries
    the given value, mirroring what the api compositor's auth middleware sets.
    """
    scope: dict[str, Any] = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "headers": [],
    }
    if auth_context is not _ABSENT:
        scope["state"] = {"auth_context": auth_context}
    request_context: RequestContext[Any, Any, Any] = RequestContext(
        request_id=1,
        meta=None,
        session=None,
        lifespan_context=None,
        request=Request(scope),
    )
    token = request_ctx.set(request_context)
    try:
        yield
    finally:
        request_ctx.reset(token)


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_tool_dispatches_with_request_auth_context(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """A tool handler dispatches with the AuthContext from the request scope."""
    mock_dispatch.return_value = None
    sentinel = _make_auth_context()
    with _request_scope(auth_context=sentinel):
        await server.fastmcp.call_tool("list_runs", {"kwargs": {}})
    mock_dispatch.assert_awaited_once()
    dispatched_context = mock_dispatch.call_args[0][0]
    assert dispatched_context.auth_context is sentinel


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_tool_dispatches_open_mode_none(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """With no state in the scope (open mode), auth_context is None."""
    mock_dispatch.return_value = None
    with _request_scope(auth_context=_ABSENT):
        await server.fastmcp.call_tool("list_runs", {"kwargs": {}})
    mock_dispatch.assert_awaited_once()
    dispatched_context = mock_dispatch.call_args[0][0]
    assert dispatched_context.auth_context is None


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_template_resource_dispatches_with_request_auth_context(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """A resource template handler dispatches with the request AuthContext."""
    mock_dispatch.return_value = None
    sentinel = _make_auth_context()
    run_id = str(uuid4())
    with _request_scope(auth_context=sentinel):
        await server.fastmcp.read_resource(f"orxtra://runs/{run_id}")
    calls = [c for c in mock_dispatch.call_args_list if c[0][1] == "get_run"]
    assert len(calls) == 1
    assert calls[0][0][0].auth_context is sentinel


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_static_resource_dispatches_with_request_auth_context(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """A static resource handler dispatches with the request AuthContext.

    Static resources take no injected Context parameter, so identity is
    obtained via ``mcp_app.get_context()``; this proves that path carries it.
    """
    mock_dispatch.return_value = {}
    sentinel = _make_auth_context()
    with _request_scope(auth_context=sentinel):
        await server.fastmcp.read_resource("orxtra://pricing")
    calls = [c for c in mock_dispatch.call_args_list if c[0][1] == "show_pricing"]
    assert len(calls) == 1
    assert calls[0][0][0].auth_context is sentinel


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_static_resource_open_mode_none(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """Static resource in open mode dispatches with auth_context=None."""
    mock_dispatch.return_value = {}
    with _request_scope(auth_context=_ABSENT):
        await server.fastmcp.read_resource("orxtra://pricing")
    calls = [c for c in mock_dispatch.call_args_list if c[0][1] == "show_pricing"]
    assert len(calls) == 1
    assert calls[0][0][0].auth_context is None


async def test_tool_call_insufficient_scope_surfaces_error(
    server: MCPServer,
) -> None:
    """Enforcement bites end-to-end: a tool call whose request identity lacks
    the capability's required scope fails through the MCP layer.

    dispatch() is NOT patched here -- the real Authorizer runs. The request
    carries an AuthContext with no scopes, so list_runs (requires runs:read)
    raises AuthorizationError inside the handler, which FastMCP surfaces as a
    ToolError.
    """
    from mcp.server.fastmcp.exceptions import ToolError

    now = datetime(2026, 1, 1, tzinfo=UTC)
    unscoped = AuthContext(
        id=uuid4(),
        consumer_id=uuid4(),
        scopes=frozenset(),
        trust_tier=TrustTier.VERIFIED,
        authenticated_via="test",
        issued_at=now,
        expires_at=None,
    )
    with _request_scope(auth_context=unscoped), pytest.raises(ToolError) as exc_info:
        await server.fastmcp.call_tool("list_runs", {"kwargs": {}})
    assert "runs:read" in str(exc_info.value)


# ------------------------------------------------------------------
# 4.7 -- Served tool input schemas
#
# Regression coverage for the bug where FastMCP registered handlers as
# ``async def handler(ctx, **kwargs)`` and therefore derived a broken input
# schema (a single required ``kwargs`` object) instead of the capability's real
# parameter schema. These tests assert on what FastMCP SERVES via list_tools
# and on end-to-end call_tool argument flow -- neither of which the legacy
# get_tool_definitions() projection ever exercised, which is how the bug
# shipped.
# ------------------------------------------------------------------


def _schema_core(schema: dict[str, Any]) -> dict[str, Any]:
    """Reduce a JSON schema to its comparable core.

    The top-level ``title`` (the pydantic model name) is the only benign
    envelope difference between the served schema and the params_model schema:
    the served model is a distinct dynamically-created model, so its name
    differs. Everything clients rely on -- ``properties`` (names, types,
    formats, defaults, descriptions), the ``required`` set, ``$defs`` for
    nested models, and ``additionalProperties`` -- must match exactly.
    """
    return {
        "properties": schema.get("properties", {}),
        "required": sorted(schema.get("required", [])),
        "defs": schema.get("$defs"),
        "additionalProperties": schema.get("additionalProperties"),
    }


async def test_served_tool_schema_matches_params_model(server: MCPServer) -> None:
    """Each tool's SERVED input schema equals its capability's params_model.

    Primary bug reproduction: against the broken handler, every served schema
    is ``{properties: {kwargs: {...}}, required: [kwargs]}`` instead of the
    capability's real parameter schema.
    """
    tools = await server.fastmcp.list_tools()
    caps = {c.name: c for c in _mcp_capabilities()}
    assert tools  # sanity: tools are registered
    for tool in tools:
        cap = caps[tool.name]
        served = _schema_core(tool.inputSchema)
        expected = _schema_core(cap.params_model.model_json_schema())
        assert served == expected, (
            f"{tool.name}: served schema does not match params_model.\n"
            f"served={served}\nexpected={expected}"
        )


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_tool_call_named_args_reach_dispatch(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """A call_tool with real named arguments flows them to dispatch verbatim.

    Against the broken handler the served schema requires a single ``kwargs``
    object, so proper named params fail validation before dispatch is reached.
    """
    mock_dispatch.return_value = None
    sentinel = _make_auth_context()
    run_id = str(uuid4())
    with _request_scope(auth_context=sentinel):
        await server.fastmcp.call_tool("get_run", {"run_id": run_id})
    mock_dispatch.assert_awaited_once()
    dispatched_context, cap_name, raw_args = mock_dispatch.call_args[0]
    assert cap_name == "get_run"
    assert raw_args == {"run_id": run_id}
    assert dispatched_context.auth_context is sentinel


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_tool_call_multi_arg_reaches_dispatch(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """A multi-parameter tool receives each named argument with its value."""
    mock_dispatch.return_value = None
    sentinel = _make_auth_context()
    with _request_scope(auth_context=sentinel):
        await server.fastmcp.call_tool(
            "start_run",
            {"config_path": "/tmp/run.toml", "intent": "do the thing"},
        )
    mock_dispatch.assert_awaited_once()
    _, cap_name, raw_args = mock_dispatch.call_args[0]
    assert cap_name == "start_run"
    assert raw_args == {"config_path": "/tmp/run.toml", "intent": "do the thing"}


@patch("orxtra.mcp._server.dispatch", new_callable=AsyncMock)
async def test_tool_call_rejects_unknown_arguments(
    mock_dispatch: AsyncMock,
    server: MCPServer,
) -> None:
    """Unknown arguments are rejected (params models are strict, extra=forbid).

    The rejection happens at the FastMCP boundary -- an unknown arg is never
    silently dropped and dispatch does not run.
    """
    from mcp.server.fastmcp.exceptions import ToolError

    mock_dispatch.return_value = None
    sentinel = _make_auth_context()
    run_id = str(uuid4())
    with _request_scope(auth_context=sentinel), pytest.raises(ToolError):
        await server.fastmcp.call_tool(
            "get_run",
            {"run_id": run_id, "bogus": 1},
        )
    mock_dispatch.assert_not_awaited()


async def test_context_param_excluded_from_tool_schema(server: MCPServer) -> None:
    """The injected Context parameter never appears in a tool's input schema."""
    tools = await server.fastmcp.list_tools()
    assert tools  # sanity: tools are registered
    for tool in tools:
        properties = tool.inputSchema.get("properties", {})
        assert "ctx" not in properties, (
            f"{tool.name}: Context param leaked into input schema"
        )


async def test_context_param_excluded_from_resource_templates(
    server: MCPServer,
) -> None:
    """The injected Context parameter never appears in a template's URI params.

    Template registration validates URI params against function params
    (excluding the Context param); successful registration of the four
    parameterized resources with only ``{run_id}`` proves ctx is excluded.
    """
    templates = await server.fastmcp.list_resource_templates()
    for template in templates:
        assert "ctx" not in template.uriTemplate
