from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-write-safety")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.write_safety._atomic import atomic_write
from orxtra.write_safety._queue import WriteQueue
from orxtra.write_safety._replay import is_transient_error, with_transient_retry
from orxtra.write_safety._stale import StaleWriteError, StaleWriteTracker, compute_hash

__all__ = [
    "__version__",
    "StaleWriteError",
    "StaleWriteTracker",
    "WriteQueue",
    "atomic_write",
    "compute_hash",
    "is_transient_error",
    "with_transient_retry",
]
