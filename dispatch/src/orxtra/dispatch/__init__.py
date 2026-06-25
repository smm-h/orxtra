from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-dispatch")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.dispatch._delivery import TransientEventDelivery
from orxtra.dispatch._memory_backend import InMemoryDispatchBackend
from orxtra.dispatch._protocols import (
    AccumulatorStorage,
    ActionStorage,
    DispatchBackend,
    SubscriptionStorage,
)
from orxtra.dispatch._types import (
    AccumulatorEntry,
    FilterPredicate,
    Subscription,
    SubscriptionAction,
)

__all__ = [
    "__version__",
    "AccumulatorEntry",
    "AccumulatorStorage",
    "ActionStorage",
    "DispatchBackend",
    "FilterPredicate",
    "InMemoryDispatchBackend",
    "Subscription",
    "SubscriptionAction",
    "SubscriptionStorage",
    "TransientEventDelivery",
]
