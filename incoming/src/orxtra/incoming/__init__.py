from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-incoming")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.incoming._receiver import create_incoming_router

__all__ = [
    "__version__",
    "create_incoming_router",
]
