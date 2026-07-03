from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-secrets")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.secrets._factory import create_secret_registry
from orxtra.secrets._mac_provider import EnvMacProvider
from orxtra.secrets._registry import SecretRegistry

__all__ = [
    "EnvMacProvider",
    "SecretRegistry",
    "__version__",
    "create_secret_registry",
]
