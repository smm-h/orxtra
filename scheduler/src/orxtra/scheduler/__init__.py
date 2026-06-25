from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-scheduler")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._graph import (
    CycleError,
    build_graph,
    find_parallel_groups,
    topological_sort,
)
from orxtra.scheduler._loader import load_workflow
from orxtra.scheduler._locks import FileLockRegistry
from orxtra.scheduler._overseer import (
    OverseerAdapter,
    OverseerInterface,
)
from orxtra.scheduler._services import (
    ServiceInstance,
    check_health,
    start_service,
    stop_service,
)
from orxtra.scheduler._types import (
    EscalationPolicy,
    ServiceConfig,
    WorkflowConfig,
)
from orxtra.scheduler._validator import validate_task_tree

__all__ = [
    "__version__",
    "CycleError",
    "EscalationPolicy",
    "FileLockRegistry",
    "OverseerAdapter",
    "OverseerInterface",
    "Scheduler",
    "ServiceConfig",
    "ServiceInstance",
    "WorkflowConfig",
    "build_graph",
    "check_health",
    "find_parallel_groups",
    "load_workflow",
    "start_service",
    "stop_service",
    "topological_sort",
    "validate_task_tree",
]
