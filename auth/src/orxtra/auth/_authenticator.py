"""Authenticator: thin dispatcher over a CredentialVerifier registry.

Each credential type (api_key, bearer, hmac) has a registered verifier.
The Authenticator hashes the presented credential to find the record,
then delegates to the appropriate verifier. Unregistered credential
types are a construction-time hard error.

Every verification emits an audit event via an optional EventSink.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from orxtra.auth._exceptions import AuthenticationError

if TYPE_CHECKING:
    from uuid import UUID

    from orxtra.auth._verifiers import HashCredentialVerifier, HmacCredentialVerifier
    from orxtra.protocols import (
        AuthContext,
        AuthStorage,
        CredentialRecord,
        EventSink,
    )

_logger = logging.getLogger("orxtra.auth")

# Union of concrete verifier types (no abstract base needed).
type AnyVerifier = HashCredentialVerifier | HmacCredentialVerifier


@dataclass(frozen=True)
class AuthAuditEvent:
    """Audit event emitted per verification attempt."""

    credential_id: str
    credential_type: str
    consumer_id: str | None
    outcome: str  # "success" | "failure"
    reason: str | None
    verified_at: datetime


class Authenticator:
    """Authenticates raw credentials against a backend via per-type verifiers.

    Construction-time hard error if a credential type has no verifier.
    """

    def __init__(
        self,
        backend: AuthStorage,
        verifiers: dict[str, AnyVerifier],
        *,
        audit_sink: EventSink[AuthAuditEvent] | None = None,
    ) -> None:
        self._backend = backend
        self._verifiers = dict(verifiers)
        self._audit_sink = audit_sink

        # Validate that all registered verifiers have matching types.
        for cred_type, verifier in self._verifiers.items():
            if verifier.credential_type != cred_type:
                msg = (
                    f"Verifier registered for {cred_type!r} reports "
                    f"credential_type={verifier.credential_type!r}"
                )
                raise ValueError(msg)

    async def verify_by_credential_id(
        self,
        credential_id: UUID,
        presented_credential: str,
    ) -> AuthContext:
        """Verify a credential looked up by ID rather than by hash.

        Used by the webhook receiver where the credential_id is known
        from the source record. The presented_credential format is the
        same as for authenticate(): raw token for bearer/api_key,
        'identifier:signature:message' for HMAC.

        Raises AuthenticationError if the credential is not found, the
        verifier is missing, or verification fails.
        """
        cred: CredentialRecord | None = await self._backend.get_credential_by_id(
            credential_id,
        )
        if cred is None:
            await self._emit_audit(
                credential_id=str(credential_id),
                credential_type="unknown",
                consumer_id=None,
                outcome="failure",
                reason="Credential not found by ID",
            )
            msg = "Credential not found"
            raise AuthenticationError(msg)

        return await self._verify_with_record(cred, presented_credential)

    async def authenticate(
        self,
        raw_credential: str,
    ) -> AuthContext:
        """Authenticate a raw credential string.

        For hash-based types (api_key, bearer), this is the raw token.
        For HMAC, this is 'identifier:signature:message'.
        """
        # For HMAC credentials, we extract the identifier part to hash.
        # For hash-based credentials, we hash the entire credential.
        identifier = raw_credential
        if ":" in raw_credential:
            # Could be HMAC format -- try extracting the identifier.
            parts = raw_credential.split(":", maxsplit=2)
            if len(parts) == 3:  # noqa: PLR2004
                identifier = parts[0]

        credential_hash = hashlib.sha256(identifier.encode()).hexdigest()
        cred: CredentialRecord | None = await self._backend.get_credential_by_hash(
            credential_hash,
        )

        if cred is None and identifier != raw_credential:
            # Try hashing the full credential (for bearer/api_key).
            full_hash = hashlib.sha256(
                raw_credential.encode(),
            ).hexdigest()
            cred = await self._backend.get_credential_by_hash(full_hash)

        if cred is None:
            await self._emit_audit(
                credential_id="unknown",
                credential_type="unknown",
                consumer_id=None,
                outcome="failure",
                reason="No matching credential found",
            )
            msg = "Invalid credential"
            raise AuthenticationError(msg)

        return await self._verify_with_record(cred, raw_credential)

    async def _verify_with_record(
        self,
        cred: CredentialRecord,
        presented_credential: str,
    ) -> AuthContext:
        """Verify a presented credential against a known credential record.

        Shared by authenticate() (hash lookup) and verify_by_credential_id()
        (ID lookup). Delegates to the per-type verifier, emits audit events.
        """
        verifier = self._verifiers.get(cred.credential_type)
        if verifier is None:
            await self._emit_audit(
                credential_id=str(cred.id),
                credential_type=cred.credential_type,
                consumer_id=str(cred.consumer_id),
                outcome="failure",
                reason=f"No verifier for type {cred.credential_type!r}",
            )
            msg = f"No verifier registered for credential type {cred.credential_type!r}"
            raise AuthenticationError(msg)

        try:
            auth_context = await verifier.verify(cred, presented_credential)
        except AuthenticationError as exc:
            await self._emit_audit(
                credential_id=str(cred.id),
                credential_type=cred.credential_type,
                consumer_id=str(cred.consumer_id),
                outcome="failure",
                reason=str(exc),
            )
            raise

        await self._emit_audit(
            credential_id=str(cred.id),
            credential_type=cred.credential_type,
            consumer_id=str(cred.consumer_id),
            outcome="success",
            reason=None,
        )
        return auth_context

    async def _emit_audit(
        self,
        *,
        credential_id: str,
        credential_type: str,
        consumer_id: str | None,
        outcome: str,
        reason: str | None,
    ) -> None:
        if self._audit_sink is None:
            return
        event = AuthAuditEvent(
            credential_id=credential_id,
            credential_type=credential_type,
            consumer_id=consumer_id,
            outcome=outcome,
            reason=reason,
            verified_at=datetime.now(tz=UTC),
        )
        try:
            await self._audit_sink.on_event(event)
        except Exception:  # noqa: BLE001
            _logger.warning(
                "Failed to emit auth audit event for credential %s",
                credential_id,
            )
