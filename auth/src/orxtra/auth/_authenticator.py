from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orxtra.protocols import Principal, TrustTier

from orxtra.auth._exceptions import AuthenticationError

if TYPE_CHECKING:
    from typing import Any

    from orxtra.auth._backend import AuthBackend
    from orxtra.auth._inmemory import InMemoryAuthBackend


class Authenticator:
    """Authenticates raw credentials against a backend."""

    def __init__(self, backend: AuthBackend | InMemoryAuthBackend) -> None:
        self._backend = backend

    async def authenticate(
        self,
        raw_credential: str,
        *,
        pool: Any = None,  # noqa: ANN401
    ) -> Principal:
        credential_hash = hashlib.sha256(raw_credential.encode()).hexdigest()

        cred = await self._backend.get_credential_by_hash(pool, credential_hash)
        if cred is None:
            msg = "Invalid credential"
            raise AuthenticationError(msg)

        consumer = await self._backend.get_consumer(pool, cred.consumer_id)
        if consumer is None:
            msg = "Consumer not found for credential"
            raise AuthenticationError(msg)

        if consumer.disabled_at is not None:
            msg = "Consumer is disabled"
            raise AuthenticationError(msg)

        now = datetime.now(tz=UTC)
        return Principal(
            id=cred.id,
            consumer_id=consumer.id,
            scopes=frozenset(consumer.scope_grants),
            trust_tier=TrustTier(consumer.trust_tier),
            authenticated_via=cred.credential_type,
            issued_at=now,
            expires_at=None,
        )
