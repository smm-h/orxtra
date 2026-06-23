from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-session")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.session._factory import create_session
from orxtra.session._pricing import PRICING_TABLE, TokenRates, compute_cost_usd
from orxtra.session._session import Session
from orxtra.session._sync import SyncSession, sync_ask

__all__ = [
    "__version__",
    "PRICING_TABLE",
    "Session",
    "SyncSession",
    "TokenRates",
    "compute_cost_usd",
    "create_session",
    "sync_ask",
]
