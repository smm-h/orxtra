from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-auth")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.auth._authenticator import Authenticator
from orxtra.auth._authorizer import Authorizer
from orxtra.auth._backend import AuthBackend, ConsumerRecord, CredentialRecord
from orxtra.auth._exceptions import AuthenticationError, AuthorizationError
from orxtra.auth._inmemory import InMemoryAuthBackend
from orxtra.auth._middleware import auth_middleware

__all__ = [
    "AuthBackend",
    "AuthenticationError",
    "AuthorizationError",
    "Authenticator",
    "Authorizer",
    "ConsumerRecord",
    "CredentialRecord",
    "InMemoryAuthBackend",
    "__version__",
    "auth_middleware",
]
