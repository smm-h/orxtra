from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-a2a")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.a2a._agent_card import build_agent_card
from orxtra.a2a._server import OrxtraRequestHandler, create_app
from orxtra.a2a._skills import SkillRegistry
from orxtra.a2a._state_bridge import TaskStateBridge

__all__ = [
    "OrxtraRequestHandler",
    "SkillRegistry",
    "TaskStateBridge",
    "__version__",
    "build_agent_card",
    "create_app",
]
