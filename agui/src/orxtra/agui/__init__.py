from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-agui")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.agui._server import create_agui_router
from orxtra.agui._sinks import AGUIOverseerSink, AGUITransportSink
from orxtra.agui._state import StateManager
from orxtra.agui._translator import AGUITranslator

__all__ = [
    "AGUIOverseerSink",
    "AGUITranslator",
    "AGUITransportSink",
    "StateManager",
    "__version__",
    "create_agui_router",
]
