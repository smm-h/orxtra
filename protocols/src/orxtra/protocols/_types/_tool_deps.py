from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from orxtra.agent import Agent
    from orxtra.protocols import Tool
    from orxtra.secrets import SecretRegistry
    from orxtra.trace import StorageBackend, TraceWriter
    from orxtra.transport import Transport
    from orxtra.write_safety import StaleWriteTracker, WriteQueue


@dataclass
class ToolDeps:
    """Session-scoped dependencies available to tool factories."""

    read_root: Path
    write_scope: list[Path] | None
    write_queue: WriteQueue
    stale_tracker: StaleWriteTracker
    session_id: str
    trace_writer: TraceWriter | StorageBackend
    run_id: UUID
    task_id: UUID
    task_name: str
    task_agent: str
    scheduler_ref: Any  # TaskSchedulerRef protocol
    transport_registry: dict[str, Transport]
    categories: dict[str, str]
    agents: dict[str, Agent]
    preview_threshold: int
    preview_lines: int
    secret_registry: SecretRegistry | None = None
