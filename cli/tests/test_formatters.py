from __future__ import annotations

import json
from datetime import UTC, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from orxt.cli._formatters import format_json, format_output, format_table


class _FakeModel:
    def __init__(self, **kwargs: object) -> None:
        self._data = kwargs

    def model_dump(self) -> dict[str, object]:
        return dict(self._data)


# -- format_json --


def test_format_json_produces_valid_json() -> None:
    data = {"name": "alice", "count": 3}
    result = format_json(data)
    parsed = json.loads(result)
    assert parsed == data


def test_format_json_nested_dicts() -> None:
    data = {"outer": {"inner": [1, 2, 3]}}
    result = format_json(data)
    parsed = json.loads(result)
    assert parsed["outer"]["inner"] == [1, 2, 3]


def test_format_json_uuid_serialization() -> None:
    uid = UUID("12345678-1234-5678-1234-567812345678")
    data = {"id": uid}
    result = format_json(data)
    parsed = json.loads(result)
    assert parsed["id"] == str(uid)


def test_format_json_decimal_serialization() -> None:
    data = {"cost": Decimal("19.99")}
    result = format_json(data)
    parsed = json.loads(result)
    assert parsed["cost"] == "19.99"


def test_format_json_datetime_serialization() -> None:
    dt = datetime(2026, 1, 15, 10, 30, 0, tzinfo=UTC)
    data = {"created": dt}
    result = format_json(data)
    parsed = json.loads(result)
    assert parsed["created"] == dt.isoformat()


def test_format_json_with_model_like_object() -> None:
    model = _FakeModel(name="run-1", status="ok")
    result = format_json(model)
    parsed = json.loads(result)
    assert parsed == {"name": "run-1", "status": "ok"}


def test_format_json_with_list_of_models() -> None:
    models = [_FakeModel(x=1), _FakeModel(x=2)]
    result = format_json(models)
    parsed = json.loads(result)
    assert parsed == [{"x": 1}, {"x": 2}]


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


# -- format_output --


def test_format_output_dispatches_to_json() -> None:
    data = {"x": 1}
    result = format_output(data, "json")
    parsed = json.loads(result)
    assert parsed == {"x": 1}


def test_format_output_dispatches_to_table() -> None:
    data = [{"col": "val"}]
    result = format_output(data, "table")
    assert "col" in result
    assert "val" in result


def test_format_output_raises_on_unknown_format() -> None:
    with pytest.raises(ValueError, match="unknown output format"):
        format_output({}, "yaml")
