"""Shared secret scrubbing for tool execution pipelines.

Both the local pipeline (tool/_pipeline.py) and the remote pipeline
(worker/_pipeline_split.py) call these functions. The drift-sentinel
test asserts that both pipelines reference this module.
"""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.protocols import ToolOutput

if TYPE_CHECKING:
    from orxtra.secrets import SecretRegistry


class _ScrubEncoder(json.JSONEncoder):
    """JSON encoder that handles common non-serializable types.

    Covers UUID, Decimal, datetime, Path, dataclasses, and
    pydantic models -- the same types _ExtendedEncoder and
    JsonRenderer._to_serializable handle in _renderers.py.
    """

    def default(self, o: object) -> Any:  # noqa: ANN401
        if isinstance(o, UUID):
            return str(o)
        if isinstance(o, Decimal):
            return str(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, Path):
            return str(o)
        if dataclasses.is_dataclass(o) and not isinstance(o, type):
            return dataclasses.asdict(o)
        if hasattr(o, "model_dump"):
            return o.model_dump()
        return super().default(o)


def scrub_text(registry: SecretRegistry, text: str) -> str:
    """Scrub secret values from a text string."""
    return registry.scrub(text)


def scrub_data(registry: SecretRegistry, data: Any) -> Any:  # noqa: ANN401
    """Scrub secret values from structured data.

    Serializes data to JSON (handling dataclasses, pydantic models,
    UUIDs, etc.), scrubs the JSON string, and deserializes back.
    The returned value is a dict/list/scalar (deserialized JSON)
    rather than the original typed object -- acceptable since data
    flows into trace/transcripts as serialized JSON anyway.

    Returns data unchanged if it is None or not JSON-serializable.
    """
    if data is None:
        return None
    try:
        serialized = json.dumps(data, cls=_ScrubEncoder)
    except (TypeError, ValueError, OverflowError):
        # Not JSON-serializable; return as-is rather than crash.
        return data
    scrubbed = registry.scrub(serialized)
    return json.loads(scrubbed)


def scrub_tool_output(
    registry: SecretRegistry,
    result: ToolOutput[Any],
) -> ToolOutput[Any]:
    """Scrub both text and data fields of a ToolOutput."""
    return ToolOutput(
        data=scrub_data(registry, result.data),
        text=scrub_text(registry, result.text),
    )
