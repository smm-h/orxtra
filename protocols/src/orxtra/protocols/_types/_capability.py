from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pydantic import BaseModel

# Infrastructure tokens a capability may declare in its ``injects`` field.
# The dispatcher maps each token to a concrete dependency it passes to the
# service function. This vocabulary will grow as new infrastructure is added.
VALID_INJECT_TOKENS: frozenset[str] = frozenset({
    "pool",
    "dispatch_backend",
    "principal_storage",
    "kind_registry",
    "notification_port",
    "get_worker_bridge",
    "run_manager",
    "caller_principal",
})


@dataclass(frozen=True)
class Capability:
    name: str
    namespace: str
    description: str
    params_model: type[BaseModel]
    result_model: type | None
    tags: frozenset[str]
    category: str
    required_scope: str
    """The scope an authenticated caller must hold to invoke this capability.

    Enforced live at the single dispatch choke point: the dispatcher
    scope-checks every call against the caller's ``AuthContext`` and rejects
    any whose scopes lack this value -- or whose ``AuthContext`` is absent
    entirely.
    """
    injects: frozenset[str]
    """The infrastructure dependencies the dispatcher passes to the service function.

    Valid tokens today: ``"pool"``, ``"dispatch_backend"``,
    ``"principal_storage"``, ``"kind_registry"``, ``"notification_port"``,
    ``"get_worker_bridge"``, ``"run_manager"``, ``"caller_principal"`` (see
    ``VALID_INJECT_TOKENS``). Most tokens map to a concrete field on the
    DispatchContext; ``"caller_principal"`` is a derived token -- the
    dispatcher resolves the authenticated caller's ``AuthContext`` to its
    persisted ``Principal`` (requiring both the auth context and principal
    storage) and passes that. An empty frozenset means a pure function that
    receives only validated params. The token vocabulary will grow later.
    """

    def __post_init__(self) -> None:
        unknown = self.injects - VALID_INJECT_TOKENS
        if unknown:
            msg = (
                f"Capability {self.name!r} declares unknown inject token(s) "
                f"{sorted(unknown)}; valid tokens are {sorted(VALID_INJECT_TOKENS)}"
            )
            raise ValueError(msg)
