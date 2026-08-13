from __future__ import annotations

import json
from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID


class _DomainEncoder(json.JSONEncoder):
    def default(self, o: object) -> Any:
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Decimal):
            return str(o)
        return super().default(o)


def _to_serializable(obj: Any) -> Any:
    if isinstance(obj, list):
        return [_to_serializable(item) for item in obj]
    if hasattr(obj, "model_dump"):
        return obj.model_dump()
    return obj


def _truncate(value: str, *, limit: int = 60) -> str:
    if len(value) <= limit:
        return value
    return value[: limit - 3] + "..."


def _format_cell(value: Any) -> str:
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


def format_table(data: Any) -> str:
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


def to_payload(data: Any) -> Any:
    """Convert a dispatch result into the plain JSON types a payload carries.

    The framework serializes the machine payload itself, so the domain types
    a capability returns -- UUIDs, datetimes, Decimals, models -- have to be
    converted before it sees them. The conversion is the same one the JSON
    rendering always did, run eagerly: encode through :class:`_DomainEncoder`
    and read the result back.
    """
    serializable = _to_serializable(data)
    return json.loads(json.dumps(serializable, cls=_DomainEncoder))
