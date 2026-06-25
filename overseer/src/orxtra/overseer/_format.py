from __future__ import annotations

import dataclasses
import json
from decimal import Decimal
from uuid import UUID


def _to_json_safe(value: object) -> object:  # noqa: PLR0911
    """Recursively convert a value to JSON-serializable form."""
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    if isinstance(value, UUID):
        return value.hex
    if isinstance(value, Decimal):
        return str(value)
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        result: dict[str, object] = {}
        for f in dataclasses.fields(value):
            field_value = getattr(value, f.name)
            if callable(field_value):
                continue
            result[f.name] = _to_json_safe(field_value)
        return result
    if isinstance(value, dict):
        return {str(k): _to_json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_json_safe(v) for v in value]
    return str(value)


def format_event(event: object) -> str:
    """Format an event dataclass as a JSON string for the Overseer.

    Handles UUID, Decimal, nested dataclasses, and other non-primitive types
    by converting them to JSON-safe representations.
    """
    event_type = type(event).__name__
    fields: dict[str, object] = {}
    if dataclasses.is_dataclass(event) and not isinstance(event, type):
        for f in dataclasses.fields(event):
            val = getattr(event, f.name)
            fields[f.name] = _to_json_safe(val)
    return json.dumps({"event_type": event_type, **fields})
