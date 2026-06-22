from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-session")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.session._factory import create_session
from orxtra.session._pricing import PRICING_TABLE, TokenRates, compute_cost_usd
from orxtra.session._session import Session

__all__ = [
    "__version__",
    "PRICING_TABLE",
    "Session",
    "TokenRates",
    "compute_cost_usd",
    "create_session",
]
