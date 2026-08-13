from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

from orxtra.cli._formatters import format_table, to_payload


class _FakeModel:
    def __init__(self, **kwargs: object) -> None:
        self._data = kwargs

    def model_dump(self) -> dict[str, object]:
        return dict(self._data)


# -- to_payload --


def test_to_payload_keeps_plain_data() -> None:
    data = {"name": "alice", "count": 3}
    assert to_payload(data) == data


def test_to_payload_nested_dicts() -> None:
    data = {"outer": {"inner": [1, 2, 3]}}
    assert to_payload(data)["outer"]["inner"] == [1, 2, 3]


def test_to_payload_uuid_becomes_a_string() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    assert to_payload({"id": uid})["id"] == str(uid)


def test_to_payload_decimal_becomes_a_string() -> None:
    # A price is a decimal and the envelope's numbers are IEEE-754 doubles,
    # so the exact value travels as a string.
    assert to_payload({"cost": Decimal("19.99")})["cost"] == "19.99"


def test_to_payload_datetime_becomes_an_iso_string() -> None:
    dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    assert to_payload({"created": dt})["created"] == dt.isoformat()


def test_to_payload_with_model_like_object() -> None:
    model = _FakeModel(name="run-1", status="ok")
    assert to_payload(model) == {"name": "run-1", "status": "ok"}


def test_to_payload_with_list_of_models() -> None:
    models = [_FakeModel(x=1), _FakeModel(x=2)]
    assert to_payload(models) == [{"x": 1}, {"x": 2}]


def test_to_payload_returns_plain_json_types() -> None:
    # The framework serializes the payload itself, so nothing domain-typed
    # may survive the conversion.
    uid = UUID("12345678-1234-5678-1234-567812345678")
    dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    payload = to_payload([{"id": uid, "at": dt, "cost": Decimal("1.50")}])
    row = payload[0]
    assert all(isinstance(v, str) for v in row.values())


# -- format_table --


def test_format_table_list_of_dicts() -> None:
    data = [{"name": "a", "value": "1"}, {"name": "b", "value": "2"}]
    result = format_table(data)
    assert "name" in result
    assert "value" in result
    assert "a" in result
    assert "b" in result
    lines = result.splitlines()
    # header, separator, two data rows
    assert len(lines) == 4


def test_format_table_empty_list() -> None:
    assert format_table([]) == "(no results)"


def test_format_table_with_model_like_objects() -> None:
    data = [_FakeModel(id="r1", status="done"), _FakeModel(id="r2", status="pending")]
    result = format_table(data)
    assert "id" in result
    assert "status" in result
    assert "r1" in result
    assert "pending" in result


def test_format_table_none_values_render_as_dash() -> None:
    data = [{"a": "hello", "b": None}]
    result = format_table(data)
    assert "-" in result


def test_format_table_single_dict_key_value() -> None:
    data = {"name": "run-42", "status": "complete"}
    result = format_table(data)
    assert "name: run-42" in result
    assert "status: complete" in result


def test_format_table_truncates_long_strings() -> None:
    long_string = "x" * 100
    data = [{"col": long_string}]
    result = format_table(data)
    # The cell should be truncated to 60 chars (57 chars + "...")
    assert "..." in result
    # The full 100-char string must not appear
    assert long_string not in result


def test_format_table_single_model() -> None:
    model = _FakeModel(key="val", num=42)
    result = format_table(model)
    assert "key: val" in result
    assert "num: 42" in result
