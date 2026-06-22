from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


class _DomainEncoder(json.JSONEncoder):
    def default(self, o: object) -> Any:  # noqa: ANN401
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def _to_serializable(obj: Any) -> Any:  # noqa: ANN401
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _truncate(value: str, *, limit: int = 60) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_cell(value: Any) -> str:  # noqa: ANN401
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (dict, list)):
        raw = json.dumps(value, cls=_DomainEncoder)
        return _truncate(raw)
    return _truncate(str(value))


def _table_from_rows(headers: list[str], rows: list[list[str]]) -> str:
    widths = [len(h) for h in headers]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))

    header_line = "  ".join(h.ljust(widths[i]) for i, h in enumerate(headers))
    separator = "  ".join("-" * w for w in widths)

    lines: list[str] = [header_line, separator]
    lines.extend(
        "  ".join(cell.ljust(widths[i]) for i, cell in enumerate(row))
        for row in rows
    )

    return "\n".join(lines)


def _table_single(data: dict[str, Any]) -> str:
    lines: list[str] = []
    for key, value in data.items():
        lines.append(f"{key}: {_format_cell(value)}")
    return "\n".join(lines)


def format_table(data: Any) -> str:  # noqa: ANN401
    if isinstance(data, list):
        if not data:
            return "(no results)"

        dicts: list[dict[str, Any]] = [
            item.model_dump() if hasattr(item, "model_dump") else item
            for item in data
        ]
        headers = list(dicts[0].keys())
        rows = [[_format_cell(d.get(h)) for h in headers] for d in dicts]
        return _table_from_rows(headers, rows)

    if hasattr(data, "model_dump"):
        return _table_single(data.model_dump())

    if isinstance(data, dict):
        return _table_single(data)

    return str(data)


def format_json(data: Any) -> str:  # noqa: ANN401
    serializable = _to_serializable(data)
    return json.dumps(serializable, indent=2, cls=_DomainEncoder)


def format_output(data: Any, fmt: str) -> str:  # noqa: ANN401
    if fmt == "table":
        return format_table(data)
    if fmt == "json":
        return format_json(data)
    msg = f"unknown output format: {fmt!r}"
    raise ValueError(msg)
