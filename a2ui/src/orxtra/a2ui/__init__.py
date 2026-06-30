from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-a2ui")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.a2ui._engine import TemplateEngine
from orxtra.a2ui._fragments import FragmentLibrary
from orxtra.a2ui._registry import SurfaceRegistry
from orxtra.a2ui._templates import default_registry
from orxtra.a2ui._tools import make_compose_surface_tool, make_render_surface_tool

__all__ = [
    "FragmentLibrary",
    "SurfaceRegistry",
    "TemplateEngine",
    "__version__",
    "default_registry",
    "make_compose_surface_tool",
    "make_render_surface_tool",
]
