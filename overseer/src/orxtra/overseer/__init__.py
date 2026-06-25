from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-overseer")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.overseer._autonomy import (
    AutonomyLevel,
)
from orxtra.overseer._health import HealthMetrics, HealthMonitor
from orxtra.overseer._knowledge import load_knowledge_files
from orxtra.overseer._overseer import Overseer, OverseerEvent, load_overseer_prompt

__all__ = [
    "__version__",
    "AutonomyLevel",
    "HealthMetrics",
    "HealthMonitor",
    "Overseer",
    "OverseerEvent",
    "load_knowledge_files",
    "load_overseer_prompt",
]
