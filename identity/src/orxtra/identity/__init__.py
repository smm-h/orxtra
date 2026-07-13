"""Identity model: persisted principals -- durable actor identity.

Deliberately minimal. Owns the durable record of who an actor is,
independent of any single credential or session. Backends and the
principal registry arrive in a later phase; this package is currently
the registered, CI-wired skeleton with no public API surface yet.
"""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-identity")
except PackageNotFoundError:
    __version__ = "0.0.0"

__all__ = [
    "__version__",
]
