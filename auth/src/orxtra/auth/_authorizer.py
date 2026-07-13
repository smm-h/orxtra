from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.auth._exceptions import AuthorizationError

if TYPE_CHECKING:
    from orxtra.protocols import AuthContext


class Authorizer:
    """Checks whether an auth context has the required scope."""

    def authorize(self, auth_context: AuthContext, required_scope: str) -> None:
        if required_scope not in auth_context.scopes:
            msg = (
                f"Consumer {auth_context.consumer_id} lacks required scope"
                f" {required_scope!r}"
            )
            raise AuthorizationError(msg)
