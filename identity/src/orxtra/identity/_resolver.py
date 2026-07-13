from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.protocols import (
    KIND_CONSUMER,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    TrustTier,
)

if TYPE_CHECKING:
    from orxtra.protocols import AuthContext, Principal, PrincipalStorage


async def resolve_caller_principal(
    auth_context: AuthContext,
    storage: PrincipalStorage,
) -> Principal:
    """Resolve an ephemeral ``AuthContext`` to its persisted ``Principal``.

    The single home for the AuthContext -> persisted-principal mapping.
    Rules:

    - SYSTEM trust tier resolves to the singleton system principal. If it is
      absent, the database was never seeded -- a hard error.
    - A non-SYSTEM context with no ``consumer_id`` is invalid: only
      system-tier contexts may omit consumer identity.
    - Otherwise the consumer's principal must exist. A consumer without a
      backing principal is an integrity violation, not a recoverable state.
    """
    if auth_context.trust_tier == TrustTier.SYSTEM:
        principal = await storage.get_principal_by_ref(
            KIND_SYSTEM,
            SYSTEM_PRINCIPAL_EXTERNAL_REF,
        )
        if principal is None:
            msg = (
                "System principal not seeded -- run 'orxtra db init' to seed "
                "the singleton system principal."
            )
            raise RuntimeError(msg)
        return principal

    if auth_context.consumer_id is None:
        msg = (
            "Non-system auth context has no consumer_id; only SYSTEM-tier "
            "contexts may omit consumer identity."
        )
        raise RuntimeError(msg)

    principal = await storage.get_principal_by_ref(
        KIND_CONSUMER,
        auth_context.consumer_id,
    )
    if principal is None:
        msg = (
            f"Consumer {auth_context.consumer_id} has no backing principal "
            f"(integrity violation): every consumer must be minted as a "
            f"principal."
        )
        raise RuntimeError(msg)
    return principal
