from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from uuid import UUID

from orxtra.mcp._server import _serialize

# pytest-asyncio auto mode (asyncio_mode = "auto" in root pyproject.toml)
# detects async test functions automatically -- no @pytest.mark.asyncio needed.
#
# The legacy JSON-RPC-over-stdio surface (run_stdio, handle_request, the
# _handlers dispatch dict, and the _handle_* methods) had zero production
# callers and bypassed the HTTP auth wall; it was deleted along with its
# tests. Tool projection, schema, FastMCP wiring, and resource behavior are
# covered by test_capability_compat.py, test_tools.py, and test_fastmcp.py.
# This module retains the _serialize serialization tests.


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


def test_serialize_tuple_recurses_and_is_json_safe() -> None:
    """A tuple (e.g. fire_event's ``(event_id, inserted)`` return) is serialized
    element-wise so ``json.dumps`` never chokes on a UUID inside a tuple.

    Regression: ``_serialize`` previously fell through tuples untouched, so an
    authenticated MCP ``fire_event`` returned an error while the event had
    already been fired.
    """
    import json
    from uuid import UUID

    uid = UUID("12345678-1234-5678-1234-567812345678")
    result = _serialize((uid, True))
    assert result == ["12345678-1234-5678-1234-567812345678", True]
    # The whole point: the serialized form is JSON-encodable.
    assert json.loads(json.dumps(result)) == [str(uid), True]
