from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from uuid6 import uuid7

from orxtra.protocols import TrustTier

from orxtra.auth._backend import ConsumerRecord, CredentialRecord


class InMemoryAuthBackend:
    """Dict-backed auth storage for tests."""

    def __init__(self) -> None:
        self._consumers: dict[UUID, ConsumerRecord] = {}
        self._credentials: dict[UUID, CredentialRecord] = {}

    async def create_consumer(
        self,
        pool: Any,  # noqa: ANN401
        name: str,
        trust_tier: TrustTier,
        scope_grants: list[str],
    ) -> UUID:
        consumer_id = uuid7()
        now = datetime.now(tz=UTC)
        self._consumers[consumer_id] = ConsumerRecord(
            id=consumer_id,
            name=name,
            trust_tier=trust_tier,
            scope_grants=scope_grants,
            disabled_at=None,
            created_at=now,
        )
        return consumer_id

    async def get_consumer(
        self,
        pool: Any,  # noqa: ANN401
        consumer_id: UUID,
    ) -> ConsumerRecord | None:
        return self._consumers.get(consumer_id)

    async def disable_consumer(
        self,
        pool: Any,  # noqa: ANN401
        consumer_id: UUID,
    ) -> None:
        existing = self._consumers.get(consumer_id)
        if existing is None:
            msg = f"Consumer {consumer_id} not found"
            raise KeyError(msg)
        now = datetime.now(tz=UTC)
        self._consumers[consumer_id] = ConsumerRecord(
            id=existing.id,
            name=existing.name,
            trust_tier=existing.trust_tier,
            scope_grants=existing.scope_grants,
            disabled_at=now,
            created_at=existing.created_at,
        )

    async def create_credential(
        self,
        pool: Any,  # noqa: ANN401
        consumer_id: UUID,
        credential_type: str,
        raw_value: str,
    ) -> UUID:
        credential_id = uuid7()
        credential_hash = hashlib.sha256(raw_value.encode()).hexdigest()
        now = datetime.now(tz=UTC)
        self._credentials[credential_id] = CredentialRecord(
            id=credential_id,
            consumer_id=consumer_id,
            credential_type=credential_type,
            credential_hash=credential_hash,
            algorithm="sha256",
            metadata={},
            created_at=now,
        )
        return credential_id

    async def get_credential_by_hash(
        self,
        pool: Any,  # noqa: ANN401
        credential_hash: str,
    ) -> CredentialRecord | None:
        for cred in self._credentials.values():
            if cred.credential_hash == credential_hash:
                return cred
        return None

    # -- Expose internals for direct test manipulation --

    def _get_consumers(self) -> dict[UUID, ConsumerRecord]:
        return self._consumers

    def _get_credentials(self) -> dict[UUID, CredentialRecord]:
        return self._credentials

    def _inject_scope_grants(self, consumer_id: UUID, scopes: list[str]) -> None:
        """Directly set scope_grants for test convenience (avoids JSON round-trip)."""
        existing = self._consumers[consumer_id]
        self._consumers[consumer_id] = ConsumerRecord(
            id=existing.id,
            name=existing.name,
            trust_tier=existing.trust_tier,
            scope_grants=json.loads(json.dumps(scopes)),
            disabled_at=existing.disabled_at,
            created_at=existing.created_at,
        )
