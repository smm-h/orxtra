from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-api")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.api._compositor import CompositorConfig, create_compositor

__all__ = [
    "CompositorConfig",
    "__version__",
    "create_compositor",
]
