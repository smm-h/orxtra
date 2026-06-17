from __future__ import annotations

import asyncio
import contextlib
import json
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest
from orxt.mcp._server import MCPServer, _serialize

# pytest-asyncio auto mode (asyncio_mode = "auto" in root pyproject.toml)
# detects async test functions automatically -- no @pytest.mark.asyncio needed.


def _rpc(
    method: str, params: dict[str, Any] | None = None, *, request_id: int = 1
) -> dict[str, Any]:
    req: dict[str, Any] = {"jsonrpc": "2.0", "method": method, "id": request_id}
    if params is not None:
        req["params"] = params
    return req


# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_pool() -> Any:  # noqa: ANN401
    return AsyncMock()


@pytest.fixture
def server(mock_pool: Any) -> MCPServer:  # noqa: ANN401
    return MCPServer(mock_pool)


# ------------------------------------------------------------------
# Initialize
# ------------------------------------------------------------------


async def test_initialize_returns_server_info(server: MCPServer) -> None:
    resp = await server.handle_request(_rpc("initialize"))
    result = resp["result"]
    assert result["protocolVersion"] == "2024-11-05"
    assert result["capabilities"]["tools"] == {}
    assert result["serverInfo"]["name"] == "orxt-mcp"
    assert result["serverInfo"]["version"] == "0.0.0"
    assert resp["jsonrpc"] == "2.0"
    assert "error" not in resp


# ------------------------------------------------------------------
# tools/list
# ------------------------------------------------------------------


async def test_tools_list_returns_all_tools(server: MCPServer) -> None:
    resp = await server.handle_request(_rpc("tools/list"))
    result = resp["result"]
    assert "tools" in result
    assert len(result["tools"]) == 20
    assert resp["jsonrpc"] == "2.0"
    assert "error" not in resp


# ------------------------------------------------------------------
# tools/call -- individual tool dispatch
# ------------------------------------------------------------------


@patch("orxt.mcp._server.list_runs", new_callable=AsyncMock)
async def test_tools_call_list_runs(
    mock_fn: AsyncMock, server: MCPServer
) -> None:
    mock_fn.return_value = []
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "list_runs", "arguments": {}})
    )
    result = resp["result"]
    content = result["content"]
    assert len(content) == 1
    assert content[0]["type"] == "text"
    assert json.loads(content[0]["text"]) == []
    assert "error" not in resp


@patch("orxt.mcp._server.get_run", new_callable=AsyncMock)
async def test_tools_call_get_run(
    mock_fn: AsyncMock, server: MCPServer
) -> None:
    mock_fn.return_value = None
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "get_run", "arguments": {"run_id": run_id}})
    )
    result = resp["result"]
    assert json.loads(result["content"][0]["text"]) is None
    assert "error" not in resp


@patch("orxt.mcp._server.abort_run", new_callable=AsyncMock)
async def test_tools_call_abort_run(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "abort_run", "arguments": {"run_id": run_id}})
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@patch("orxt.mcp._server.pause_run", new_callable=AsyncMock)
async def test_tools_call_pause_run(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "pause_run", "arguments": {"run_id": run_id}})
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@patch("orxt.mcp._server.resume_run", new_callable=AsyncMock)
async def test_tools_call_resume_run(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "resume_run", "arguments": {"run_id": run_id}})
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@patch("orxt.mcp._server.list_inbox", new_callable=AsyncMock)
async def test_tools_call_list_inbox(
    mock_fn: AsyncMock, server: MCPServer
) -> None:
    mock_fn.return_value = []
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "list_inbox", "arguments": {"run_id": run_id}})
    )
    assert json.loads(resp["result"]["content"][0]["text"]) == []
    assert "error" not in resp


@patch("orxt.mcp._server.get_inbox_item", new_callable=AsyncMock)
async def test_tools_call_get_inbox_item(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    item_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "get_inbox_item", "arguments": {"item_id": item_id}},
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, item_id=UUID(item_id))


@patch("orxt.mcp._server.respond_to_inbox", new_callable=AsyncMock)
async def test_tools_call_respond_to_inbox(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    item_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {
                "name": "respond_to_inbox",
                "arguments": {"item_id": item_id, "answer": "yes"},
            },
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(
        mock_pool, item_id=UUID(item_id), answer="yes"
    )


@patch("orxt.mcp._server.skip_inbox_item", new_callable=AsyncMock)
async def test_tools_call_skip_inbox_item(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    item_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "skip_inbox_item", "arguments": {"item_id": item_id}},
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, item_id=UUID(item_id))


@patch("orxt.mcp._server.reject_inbox_item", new_callable=AsyncMock)
async def test_tools_call_reject_inbox_item(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = None
    item_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {
                "name": "reject_inbox_item",
                "arguments": {"item_id": item_id, "reason": "invalid"},
            },
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(
        mock_pool, item_id=UUID(item_id), reason="invalid"
    )


@patch("orxt.mcp._server.TraceWriter")
@patch("orxt.mcp._server.fire_event", new_callable=AsyncMock)
async def test_tools_call_fire_event(
    mock_fn: AsyncMock, mock_writer_cls: Any, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = uuid4()
    mock_writer = mock_writer_cls.return_value
    run_id = str(uuid4())
    payload = {"key": "value"}
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {
                "name": "fire_event",
                "arguments": {
                    "run_id": run_id,
                    "event_name": "deploy",
                    "payload": payload,
                },
            },
        )
    )
    assert "error" not in resp
    mock_writer_cls.assert_called_once_with(mock_pool)
    mock_fn.assert_awaited_once_with(
        mock_writer, run_id=UUID(run_id), event_name="deploy", payload=payload
    )


@patch("orxt.mcp._server.show_pricing", new_callable=AsyncMock)
async def test_tools_call_show_pricing(
    mock_fn: AsyncMock, server: MCPServer
) -> None:
    pricing = {"gpt-4": {"input": "0.01"}}
    mock_fn.return_value = pricing
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "show_pricing", "arguments": {}})
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed == pricing
    assert "error" not in resp


@patch("orxt.mcp._server.dump_config", new_callable=AsyncMock)
async def test_tools_call_show_config(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = {"timeout": 30}
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "show_config", "arguments": {"run_id": run_id}},
        )
    )
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed == {"timeout": 30}
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@patch("orxt.mcp._server.query_events", new_callable=AsyncMock)
async def test_tools_call_query_events(
    mock_fn: AsyncMock, server: MCPServer
) -> None:
    mock_fn.return_value = []
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "query_events", "arguments": {"run_id": run_id}},
        )
    )
    assert json.loads(resp["result"]["content"][0]["text"]) == []
    assert "error" not in resp


@patch("orxt.mcp._server.get_transcript", new_callable=AsyncMock)
async def test_tools_call_get_transcript(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = []
    session_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "get_transcript", "arguments": {"session_id": session_id}},
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, session_id=UUID(session_id))


@patch("orxt.mcp._server.search_transcript", new_callable=AsyncMock)
async def test_tools_call_search_transcript(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = []
    session_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {
                "name": "search_transcript",
                "arguments": {"session_id": session_id, "query": "error"},
            },
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(
        mock_pool, session_id=UUID(session_id), query="error"
    )


@patch("orxt.mcp._server.list_tasks", new_callable=AsyncMock)
async def test_tools_call_list_tasks(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = []
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "list_tasks", "arguments": {"run_id": run_id}},
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@patch("orxt.mcp._server.get_task_attempts", new_callable=AsyncMock)
async def test_tools_call_get_task_attempts(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = []
    task_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "get_task_attempts", "arguments": {"task_id": task_id}},
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, task_id=UUID(task_id))


@patch("orxt.mcp._server.get_notepad", new_callable=AsyncMock)
async def test_tools_call_get_notepad(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    mock_fn.return_value = []
    run_id = str(uuid4())
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {"name": "get_notepad", "arguments": {"run_id": run_id}},
        )
    )
    assert "error" not in resp
    mock_fn.assert_awaited_once_with(mock_pool, run_id=UUID(run_id))


@patch("orxt.mcp._server.start_run_from_file", new_callable=AsyncMock)
async def test_tools_call_start_run(
    mock_fn: AsyncMock, server: MCPServer, mock_pool: Any  # noqa: ANN401
) -> None:
    expected_id = uuid4()
    mock_fn.return_value = expected_id
    resp = await server.handle_request(
        _rpc(
            "tools/call",
            {
                "name": "start_run",
                "arguments": {
                    "intent": "deploy staging",
                    "config_path": "/etc/orxt/run.toml",
                },
            },
        )
    )
    assert "error" not in resp
    parsed = json.loads(resp["result"]["content"][0]["text"])
    assert parsed == str(expected_id)
    mock_fn.assert_awaited_once_with(
        mock_pool, intent="deploy staging", config_path=Path("/etc/orxt/run.toml")
    )


# ------------------------------------------------------------------
# tools/call -- error cases
# ------------------------------------------------------------------


async def test_tools_call_unknown_tool_returns_error(server: MCPServer) -> None:
    resp = await server.handle_request(
        _rpc("tools/call", {"name": "nonexistent", "arguments": {}})
    )
    assert resp["error"]["code"] == -32603
    assert "Unknown tool" in resp["error"]["message"]


async def test_tools_call_missing_params(server: MCPServer) -> None:
    resp = await server.handle_request(_rpc("tools/call"))
    assert resp["error"]["code"] == -32603
    assert "Missing params" in resp["error"]["message"]


async def test_tools_call_missing_tool_name(server: MCPServer) -> None:
    resp = await server.handle_request(
        _rpc("tools/call", {"arguments": {}})
    )
    assert resp["error"]["code"] == -32603
    assert "Missing tool name" in resp["error"]["message"]


# ------------------------------------------------------------------
# JSON-RPC protocol errors
# ------------------------------------------------------------------


async def test_invalid_jsonrpc_version(server: MCPServer) -> None:
    resp = await server.handle_request(
        {"jsonrpc": "1.0", "method": "initialize", "id": 1}
    )
    assert resp["error"]["code"] == -32600
    assert "Invalid JSON-RPC version" in resp["error"]["message"]


async def test_missing_method(server: MCPServer) -> None:
    resp = await server.handle_request({"jsonrpc": "2.0", "id": 1})
    assert resp["error"]["code"] == -32600
    assert "Missing" in resp["error"]["message"]


async def test_unknown_method(server: MCPServer) -> None:
    resp = await server.handle_request(_rpc("foo/bar"))
    assert resp["error"]["code"] == -32601
    assert "Unknown method" in resp["error"]["message"]


async def test_jsonrpc_id_preserved(server: MCPServer) -> None:
    resp = await server.handle_request(_rpc("initialize", request_id=42))
    assert resp["id"] == 42


async def test_error_response_preserves_id(server: MCPServer) -> None:
    resp = await server.handle_request(_rpc("foo/bar", request_id=99))
    assert resp["id"] == 99
    assert resp["error"]["code"] == -32601


async def test_missing_jsonrpc_field(server: MCPServer) -> None:
    resp = await server.handle_request({"method": "initialize", "id": 1})
    assert resp["error"]["code"] == -32600


async def test_non_string_method(server: MCPServer) -> None:
    resp = await server.handle_request(
        {"jsonrpc": "2.0", "method": 123, "id": 1}
    )
    assert resp["error"]["code"] == -32600


# ------------------------------------------------------------------
# _serialize
# ------------------------------------------------------------------


def test_serialize_uuid() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert _serialize(uid) == "12345678-1234-5678-1234-567812345678"


def test_serialize_decimal() -> None:
    assert _serialize(Decimal("1.23")) == "1.23"


def test_serialize_datetime() -> None:
    dt = datetime(2026, 1, 15, 12, 30, 0, tzinfo=UTC)
    assert _serialize(dt) == "2026-01-15T12:30:00+00:00"


def test_serialize_path() -> None:
    assert _serialize(Path("/foo/bar")) == "/foo/bar"


def test_serialize_none() -> None:
    assert _serialize(None) is None


def test_serialize_plain_types_passthrough() -> None:
    assert _serialize(42) == 42
    assert _serialize("hello") == "hello"
    assert _serialize(True) is True
    assert _serialize(3.14) == 3.14


def test_serialize_nested_list() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    result = _serialize([{"id": uid, "amount": Decimal("9.99")}])
    assert result == [{"id": "12345678-1234-5678-1234-567812345678", "amount": "9.99"}]


def test_serialize_nested_dict() -> None:
    dt = datetime(2025, 6, 1, 0, 0, 0, tzinfo=UTC)
    result = _serialize({"created": dt, "path": Path("/test/path/x")})
    assert result == {"created": "2025-06-01T00:00:00+00:00", "path": "/test/path/x"}


def test_serialize_empty_containers() -> None:
    assert _serialize([]) == []
    assert _serialize({}) == {}


# ------------------------------------------------------------------
# PG listener
# ------------------------------------------------------------------


async def test_pg_listener_forwards_notifications() -> None:
    """PG notifications are forwarded as JSON-RPC notifications."""
    # Create mock writer
    written: list[bytes] = []

    class MockWriter:
        def write(self, data: bytes) -> None:
            written.append(data)
        async def drain(self) -> None:
            pass

    # Create mock connection that captures the listener callback
    callbacks: list[Any] = []

    class MockConn:
        async def add_listener(self, channel: str, callback: Any) -> None:
            callbacks.append((channel, callback))

    class MockPool:
        async def acquire(self) -> MockConn:
            return MockConn()
        async def release(self, conn: Any) -> None:
            pass

    server = MCPServer(pool=MockPool())
    writer = MockWriter()

    # Start the listener
    task = await server._start_pg_listener(writer)  # noqa: SLF001

    # Give it time to acquire and register
    await asyncio.sleep(0.05)

    # Verify callback was registered
    assert len(callbacks) == 1
    assert callbacks[0][0] == "orxt_events"

    # Simulate a notification
    callback_fn = callbacks[0][1]
    callback_fn(None, 0, "orxt_events", '{"event_type": "task_completed"}')

    # Give drain time to complete
    await asyncio.sleep(0.05)

    # Check output
    assert len(written) == 1
    notification = json.loads(written[0].decode())
    assert notification["jsonrpc"] == "2.0"
    assert notification["method"] == "notifications/event"
    assert notification["params"]["event_type"] == "task_completed"
    assert "id" not in notification

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
