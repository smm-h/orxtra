from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-agent")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.agent._categories import load_categories, resolve_category
from orxtra.agent._loader import load_agent, load_agents
from orxtra.agent._types import Agent, InlineToolDefinition

__all__ = [
    "Agent",
    "InlineToolDefinition",
    "__version__",
    "load_agent",
    "load_agents",
    "load_categories",
    "resolve_category",
]
