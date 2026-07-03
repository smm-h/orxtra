from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-dispatch")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.dispatch._action_executor import (
    execute_action,
    execute_actions_bounded,
)
from orxtra.protocols import ActionExecutor, EventFireCallback
from orxtra.dispatch._delivery import (
    DualPhaseEventDelivery,
    TransientEventDelivery,
    match_subscription,
)
from orxtra.dispatch._dispatch_worker import DispatchWorker
from orxtra.dispatch._memory_backend import InMemoryDispatchBackend
from orxtra.dispatch._pg_backend import PgDispatchBackend
from orxtra.dispatch._protocols import (
    AccumulatorStorage,
    ActionStorage,
    DispatchBackend,
    SourceStorage,
    SubscriptionStorage,
)
from orxtra.dispatch._types import (
    AccumulatorEntry,
    FilterPredicate,
    Source,
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
    "DispatchWorker",
    "DualPhaseEventDelivery",
    "EventFireCallback",
    "FilterPredicate",
    "InMemoryDispatchBackend",
    "PgDispatchBackend",
    "Source",
    "SourceStorage",
    "Subscription",
    "SubscriptionAction",
    "SubscriptionStorage",
    "TransientEventDelivery",
    "execute_action",
    "execute_actions_bounded",
    "match_subscription",
]
