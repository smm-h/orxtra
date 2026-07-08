from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-auth")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.auth._authenticator import AuthAuditEvent, Authenticator
from orxtra.auth._authorizer import Authorizer
from orxtra.auth._backend import AuthBackend
from orxtra.auth._exceptions import AuthenticationError, AuthorizationError
from orxtra.auth._inmemory import InMemoryAuthBackend
from orxtra.auth._middleware import auth_middleware
from orxtra.auth._verifiers import HashCredentialVerifier, HmacCredentialVerifier
from orxtra.protocols import ConsumerRecord, CredentialRecord

__all__ = [
    "AuthAuditEvent",
    "AuthBackend",
    "AuthenticationError",
    "Authenticator",
    "AuthorizationError",
    "Authorizer",
    "ConsumerRecord",
    "CredentialRecord",
    "HashCredentialVerifier",
    "HmacCredentialVerifier",
    "InMemoryAuthBackend",
    "__version__",
    "auth_middleware",
]
