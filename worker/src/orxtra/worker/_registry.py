"""Worker registry: tracks connected workers and enforces one-per-root.

Each worker registers with a project root.  Only one worker may be
registered per root at a time.  This constraint provides cross-process
write safety by assignment -- workers run their own WriteQueue and
StaleWriteTracker locally, and the one-worker-per-root rule prevents
conflicting writes without distributed locking.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from orxtra.protocols import ToolCapability

if TYPE_CHECKING:
    from orxtra.worker._brain import BrainWorkerBridge

_logger = logging.getLogger("orxtra.worker.registry")


class WorkerConflictError(Exception):
    """Raised when a second worker tries to register for the same root."""


@dataclass
class WorkerInfo:
    """State for a single connected worker."""

    id: UUID
    consumer_id: str
    root: str
    capabilities: list[ToolCapability]
    bridge: BrainWorkerBridge
    assigned_tasks: set[UUID] = field(default_factory=set)


class WorkerRegistry:
    """In-memory registry of connected workers.

    Enforces the one-worker-per-root constraint and provides
    lookup by worker ID or project root.
    """

    def __init__(self) -> None:
        self._workers: dict[UUID, WorkerInfo] = {}
        self._root_to_worker: dict[str, UUID] = {}

    def register(
        self,
        consumer_id: str,
        root: str,
        capabilities: list[ToolCapability],
        bridge: BrainWorkerBridge,
    ) -> UUID:
        """Register a new worker.  Returns the assigned worker ID.

        Raises WorkerConflictError if a worker is already registered
        for the given root.
        """
        existing = self._root_to_worker.get(root)
        if existing is not None:
            existing_info = self._workers.get(existing)
            # Allow re-registration from the same consumer (reconnection).
            if existing_info is not None and existing_info.consumer_id == consumer_id:
                _logger.info(
                    "Re-registration from consumer %s for root %s"
                    " (replacing worker %s)",
                    consumer_id, root, existing,
                )
                self._workers.pop(existing, None)
                self._root_to_worker.pop(root, None)
            else:
                msg = (
                    f"Root '{root}' already has a registered worker"
                    f" (worker_id={existing})"
                )
                raise WorkerConflictError(msg)

        worker_id = uuid4()
        info = WorkerInfo(
            id=worker_id,
            consumer_id=consumer_id,
            root=root,
            capabilities=capabilities,
            bridge=bridge,
        )
        self._workers[worker_id] = info
        self._root_to_worker[root] = worker_id

        _logger.info(
            "Registered worker %s for root %s (consumer=%s)",
            worker_id, root, consumer_id,
        )
        return worker_id

    def unregister(self, worker_id: UUID) -> None:
        """Remove a worker from the registry."""
        info = self._workers.pop(worker_id, None)
        if info is not None:
            self._root_to_worker.pop(info.root, None)
            _logger.info(
                "Unregistered worker %s for root %s",
                worker_id, info.root,
            )

    def get_worker(self, worker_id: UUID) -> WorkerInfo | None:
        """Look up a worker by ID."""
        return self._workers.get(worker_id)

    def get_worker_for_root(self, root: str) -> WorkerInfo | None:
        """Look up the worker registered for a project root."""
        worker_id = self._root_to_worker.get(root)
        if worker_id is None:
            return None
        return self._workers.get(worker_id)

    def assign_task(self, worker_id: UUID, task_id: UUID) -> None:
        """Record that a task is assigned to a worker."""
        info = self._workers.get(worker_id)
        if info is None:
            msg = f"Worker {worker_id} not found"
            raise KeyError(msg)
        info.assigned_tasks.add(task_id)

    def unassign_task(self, worker_id: UUID, task_id: UUID) -> None:
        """Remove a task assignment from a worker."""
        info = self._workers.get(worker_id)
        if info is not None:
            info.assigned_tasks.discard(task_id)

    @property
    def worker_count(self) -> int:
        return len(self._workers)

    def list_workers(self) -> list[WorkerInfo]:
        return list(self._workers.values())
