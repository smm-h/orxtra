from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-dispatch")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.dispatch._action_executor import (
    ActionExecutor,
    execute_action,
    execute_actions_bounded,
)
from orxtra.dispatch._delivery import (
    DualPhaseEventDelivery,
    TransientEventDelivery,
    match_subscription,
)
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
    "ActionExecutor",
    "ActionStorage",
    "DispatchBackend",
    "DualPhaseEventDelivery",
    "FilterPredicate",
    "InMemoryDispatchBackend",
    "Subscription",
    "SubscriptionAction",
    "SubscriptionStorage",
    "TransientEventDelivery",
    "execute_action",
    "execute_actions_bounded",
    "match_subscription",
]
