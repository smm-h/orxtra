from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-overseer")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.overseer._autonomy import (
    AUTONOMY_RULES,
    AutonomyLevel,
    is_autonomous,
    requires_approval,
)
from orxtra.overseer._health import HealthMetrics, HealthMonitor
from orxtra.overseer._overseer import Overseer, OverseerEvent, load_overseer_prompt

__all__ = [
    "__version__",
    "AUTONOMY_RULES",
    "AutonomyLevel",
    "HealthMetrics",
    "HealthMonitor",
    "Overseer",
    "OverseerEvent",
    "is_autonomous",
    "load_overseer_prompt",
    "requires_approval",
]
