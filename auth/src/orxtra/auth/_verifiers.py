"""Per-credential-type verification strategies.

HashCredentialVerifier: for bearer/api_key credentials stored as
SHA-256 hashes. Needs zero secret capability.

HmacCredentialVerifier: for HMAC credentials verified via a
KeyedMacProvider. The provider is injected at construction; the
verifier never sees raw key material.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orxtra.auth._exceptions import AuthenticationError
from orxtra.protocols import (
    AuthContext,
    MacOutcome,
    TrustTier,
)

if TYPE_CHECKING:
    from orxtra.protocols import (
        AuthStorage,
        ConsumerRecord,
        CredentialRecord,
        KeyedMacProvider,
        MacVerdict,
    )


class HashCredentialVerifier:
    """Verifies bearer/api_key credentials via SHA-256 hash comparison.

    Needs zero secret capability -- the hash is stored at rest and
    compared against the hash of the presented credential.
    """

    def __init__(
        self,
        credential_type: str,
        backend: AuthStorage,
    ) -> None:
        self._credential_type = credential_type
        self._backend = backend

    @property
    def credential_type(self) -> str:
        return self._credential_type

    async def verify(
        self,
        credential_record: CredentialRecord,
        presented_credential: str,
    ) -> AuthContext:
        # Hash the presented credential and compare.
        presented_hash = hashlib.sha256(
            presented_credential.encode(),
        ).hexdigest()
        if credential_record.credential_hash != presented_hash:
            msg = "Credential hash mismatch"
            raise AuthenticationError(msg)

        # Look up the consumer.
        consumer = await self._backend.get_consumer(
            credential_record.consumer_id,
        )
        if consumer is None:
            msg = "Consumer not found for credential"
            raise AuthenticationError(msg)
        if consumer.disabled_at is not None:
            msg = "Consumer is disabled"
            raise AuthenticationError(msg)

        return _build_auth_context(credential_record, consumer)


class HmacCredentialVerifier:
    """Verifies HMAC credentials via a KeyedMacProvider.

    The provider is injected at construction. The verifier extracts
    the secret_ref from the credential record and delegates to the
    provider's verify() method. Raw key material never crosses into
    the auth module.
    """

    def __init__(
        self,
        mac_provider: KeyedMacProvider,
        backend: AuthStorage,
    ) -> None:
        self._mac_provider = mac_provider
        self._backend = backend

    @property
    def credential_type(self) -> str:
        return "hmac"

    async def verify(
        self,
        credential_record: CredentialRecord,
        presented_credential: str,
    ) -> AuthContext:
        if credential_record.secret_ref is None:
            msg = (
                "HMAC credential has no secret_ref -- "
                "cannot verify without a key reference"
            )
            raise AuthenticationError(msg)

        # The presented_credential for HMAC is the signature.
        # The message that was signed must be provided separately
        # in a real webhook flow. For API-level HMAC auth, the
        # credential_hash stores a hash of the identifier, and the
        # presented credential is "identifier:signature:message".
        parts = presented_credential.split(":", maxsplit=2)
        if len(parts) != 3:  # noqa: PLR2004
            msg = (
                "HMAC credential must be in format "
                "'identifier:signature:message'"
            )
            raise AuthenticationError(msg)

        _identifier, signature, message = parts

        verdict: MacVerdict = await self._mac_provider.verify(
            key_ref=credential_record.secret_ref,
            message=message.encode(),
            signature=signature,
            algorithm=credential_record.algorithm,
        )

        if verdict.outcome != MacOutcome.MATCH:
            msg = "HMAC signature mismatch"
            raise AuthenticationError(msg)

        consumer = await self._backend.get_consumer(
            credential_record.consumer_id,
        )
        if consumer is None:
            msg = "Consumer not found for credential"
            raise AuthenticationError(msg)
        if consumer.disabled_at is not None:
            msg = "Consumer is disabled"
            raise AuthenticationError(msg)

        return _build_auth_context(credential_record, consumer)


def _build_auth_context(
    credential: CredentialRecord,
    consumer: ConsumerRecord,
) -> AuthContext:
    now = datetime.now(tz=UTC)
    return AuthContext(
        id=credential.id,
        consumer_id=consumer.id,
        scopes=frozenset(consumer.scope_grants),
        trust_tier=TrustTier(consumer.trust_tier),
        authenticated_via=credential.credential_type,
        issued_at=now,
        expires_at=None,
    )
