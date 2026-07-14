from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-worker")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.worker._brain import (
    BrainWorkerBridge,
    ToolCallTimeoutError,
    WorkerDisconnectedError,
)
from orxtra.worker._cli import register_worker_commands
from orxtra.worker._docker import ContainerExitError, DockerNotFoundError, DockerWorker
from orxtra.worker._native import NativeWorker, build_worker_tools
from orxtra.worker._pipeline_split import (
    should_route_to_worker,
    wrap_tool_for_remote,
    wrap_tools_for_remote,
)
from orxtra.worker._protocol import (
    ExecuteToolCall,
    Heartbeat,
    HeartbeatAck,
    ToolCallResult,
    WorkerRegistration,
)
from orxtra.worker._registry import WorkerConflictError, WorkerInfo, WorkerRegistry

__all__ = [
    "BrainWorkerBridge",
    "ContainerExitError",
    "DockerNotFoundError",
    "DockerWorker",
    "ExecuteToolCall",
    "Heartbeat",
    "HeartbeatAck",
    "NativeWorker",
    "ToolCallResult",
    "ToolCallTimeoutError",
    "WorkerConflictError",
    "WorkerDisconnectedError",
    "WorkerInfo",
    "WorkerRegistration",
    "WorkerRegistry",
    "__version__",
    "build_worker_tools",
    "register_worker_commands",
    "should_route_to_worker",
    "wrap_tool_for_remote",
    "wrap_tools_for_remote",
]
