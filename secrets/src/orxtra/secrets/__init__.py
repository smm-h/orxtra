from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-secrets")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.secrets._factory import create_secret_registry
from orxtra.secrets._registry import SecretRegistry

__all__ = ["__version__", "SecretRegistry", "create_secret_registry"]
