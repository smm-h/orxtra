"""Identity model: persisted principals -- durable actor identity.

Owns the durable record of who an actor is, independent of any single
credential or session. Provides the storage backends (PG and in-memory),
the kind registry (validation mechanism), the caller resolver
(AuthContext -> persisted Principal), and the delete-translation domain
error.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-identity")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.identity._backend import PgPrincipalStorage
from orxtra.identity._exceptions import PrincipalInUseError
from orxtra.identity._inmemory import InMemoryPrincipalStorage
from orxtra.identity._registry import KindRegistry
from orxtra.identity._resolver import resolve_caller_principal

__all__ = [
    "InMemoryPrincipalStorage",
    "KindRegistry",
    "PgPrincipalStorage",
    "PrincipalInUseError",
    "__version__",
    "resolve_caller_principal",
]
