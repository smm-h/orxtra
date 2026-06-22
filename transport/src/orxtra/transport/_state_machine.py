from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class TransportState(StrEnum):
    CALLING_API = "calling_api"
    EXECUTING_TOOLS = "executing_tools"
    SUSPENDED = "suspended"
    DONE = "done"


@dataclass
class Continuation:
    """State captured during suspension for later resumption."""

    executed_results: list[dict[str, Any]]
    remaining_blocks: list[Any]
    await_result_slot: str | None = None
    session_id: str | None = None
    messages: list[dict[str, Any]] = field(default_factory=list)
