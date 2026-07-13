"""TraceSink -- EventSink[OverseerEvent] that writes events to trace.

Replaces the old event_callback pattern on TraceWriter/PgBackend/InMemoryBackend.

DESIGN NOTE: The removed event_callback was a notification mechanism that
fired AFTER trace writes happened (write_event, transition_run,
transition_task). It propagated trace-level events to listeners with
signature (event_id, run_id, event_type, data) -> Awaitable[None].

TraceSink serves the opposite direction: it RECEIVES OverseerEvent objects
(RunStarted, TaskEscalated, etc.) and WRITES them to trace as new events.
The old event_callback was unused in production (no caller ever passed it),
so removing it has no behavioral impact.
"""

from __future__ import annotations

import dataclasses
import logging
import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.protocols import OverseerEvent
    from orxtra.trace._writer import TraceWriter

_logger = logging.getLogger("orxtra.trace")

# Pre-compiled regex for camel-to-snake conversion
_CAMEL_RE1 = re.compile(r"(.)([A-Z][a-z]+)")
_CAMEL_RE2 = re.compile(r"([a-z0-9])([A-Z])")


def _to_snake_case(name: str) -> str:
    """Convert CamelCase to snake_case."""
    s1 = _CAMEL_RE1.sub(r"\1_\2", name)
    return _CAMEL_RE2.sub(r"\1_\2", s1).lower()


def _serialize_event(event: OverseerEvent) -> dict[str, Any]:
    """Serialize a dataclass event to a JSON-safe dict."""
    if not dataclasses.is_dataclass(event) or isinstance(event, type):
        return {}
    result: dict[str, Any] = {}
    for field in dataclasses.fields(event):
        val = getattr(event, field.name)
        # Convert non-JSON-serializable types to string
        json_types = (str, int, float, bool, type(None), dict, list)
        result[field.name] = val if isinstance(val, json_types) else str(val)
    return result


class TraceSink:
    """EventSink[OverseerEvent] that persists events via TraceWriter.

    Transforms each OverseerEvent into a write_event call with:
    - event_type: snake_case of the class name (e.g., RunStarted -> run_started)
    - data: serialized fields of the event dataclass
    """

    def __init__(
        self,
        trace_writer: TraceWriter,
        run_id: UUID,
        run_principal_id: UUID,
    ) -> None:
        self._trace_writer = trace_writer
        self._run_id = run_id
        self._run_principal_id = run_principal_id

    async def on_event(self, event: OverseerEvent) -> None:
        """Write the OverseerEvent to trace storage, attributed to the run."""
        event_type = _to_snake_case(type(event).__name__)
        data = _serialize_event(event)
        await self._trace_writer.write_event(
            run_id=self._run_id,
            event_type=event_type,
            data=data,
            principal_id=self._run_principal_id,
        )
