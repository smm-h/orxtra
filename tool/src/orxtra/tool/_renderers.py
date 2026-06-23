from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID


class _ExtendedEncoder(json.JSONEncoder):
    """Handles UUID, Decimal, datetime, Path."""

    def default(self, o: object) -> Any:  # noqa: ANN401
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        return super().default(o)


class TextRenderer:
    """Renders data as plain text."""

    def render(self, data: Any) -> str:  # noqa: ANN401
        if isinstance(data, str):
            return data
        # Confirmation-like objects: prefer .message attribute
        if hasattr(data, "message") and isinstance(data.message, str):
            return data.message
        return str(data)


class JsonRenderer:
    """Renders data as indented JSON."""

    def __init__(self, indent: int = 2) -> None:
        self._indent = indent

    def render(self, data: Any) -> str:  # noqa: ANN401
        obj = self._to_serializable(data)
        return json.dumps(obj, cls=_ExtendedEncoder, indent=self._indent)

    @staticmethod
    def _to_serializable(data: Any) -> Any:  # noqa: ANN401
        if dataclasses.is_dataclass(data) and not isinstance(data, type):
            return dataclasses.asdict(data)
        if hasattr(data, "model_dump"):
            return data.model_dump()
        return data


class TableRenderer:
    """Renders list-of-dicts or DirListing as tab-separated text."""

    def render(self, data: Any) -> str:  # noqa: ANN401
        rows = self._extract_rows(data)
        if not rows:
            return ""
        headers = list(rows[0].keys())
        lines = ["\t".join(headers)]
        for row in rows:
            lines.append("\t".join(str(row.get(h, "")) for h in headers))
        return "\n".join(lines)

    @staticmethod
    def _extract_rows(data: Any) -> list[dict[str, Any]]:  # noqa: ANN401
        # DirListing-like: has .entries that are dataclasses
        if hasattr(data, "entries"):
            entries = data.entries
            if entries and dataclasses.is_dataclass(entries[0]):
                return [dataclasses.asdict(e) for e in entries]
            return [dict(e) if isinstance(e, dict) else {"value": str(e)} for e in entries]
        # Already a list of dicts
        if isinstance(data, list):
            result: list[dict[str, Any]] = []
            for item in data:
                if isinstance(item, dict):
                    result.append(item)
                elif dataclasses.is_dataclass(item) and not isinstance(item, type):
                    result.append(dataclasses.asdict(item))
                else:
                    result.append({"value": str(item)})
            return result
        return []
