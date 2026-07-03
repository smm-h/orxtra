from __future__ import annotations

from typing import TYPE_CHECKING

from orxtra.auth._exceptions import AuthorizationError

if TYPE_CHECKING:
    from orxtra.protocols import Principal


class Authorizer:
    """Checks whether a principal has the required scope."""

    def authorize(self, principal: Principal, required_scope: str) -> None:
        if required_scope not in principal.scopes:
            msg = (
                f"Principal {principal.id} lacks required scope"
                f" {required_scope!r}"
            )
            raise AuthorizationError(msg)
