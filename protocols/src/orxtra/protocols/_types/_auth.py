from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from uuid import UUID


class TrustTier(StrEnum):
    ANONYMOUS = "anonymous"
    IDENTIFIED = "identified"
    VERIFIED = "verified"
    SYSTEM = "system"


class MacOutcome(StrEnum):
    """Result of a MAC verification operation."""

    MATCH = "match"
    MISMATCH = "mismatch"


@dataclass(frozen=True)
class MacVerdict:
    """Result of a keyed MAC verification.

    Reports outcome, which secret was used, the algorithm, and
    optionally which key version matched (for rotation support).
    """

    outcome: MacOutcome
    secret_name: str
    algorithm: str
    verified_at: datetime
    matched_version: int | None = None


@dataclass(frozen=True)
class ConsumerRecord:
    """A consumer (API client) stored in the auth backend."""

    id: UUID
    principal_id: UUID
    name: str
    trust_tier: TrustTier
    scope_grants: list[str]
    disabled_at: datetime | None
    created_at: datetime


@dataclass(frozen=True)
class CredentialRecord:
    """A credential stored in the auth backend."""

    id: UUID
    consumer_id: UUID
    credential_type: str
    credential_hash: str
    algorithm: str
    metadata: dict[str, object]
    secret_ref: str | None
    created_at: datetime


@dataclass(frozen=True)
class AuthContext:
    """Ephemeral authenticated-request context.

    Created per request when a credential is verified; never persisted.
    Carries the scopes, trust tier, and identity resolved for the caller
    of a single request.

    consumer_id is None only for system-tier contexts (a future
    system/operator context with no backing consumer record); for all
    consumer-backed requests it is the resolved consumer's id.
    """

    id: UUID
    consumer_id: UUID | None
    scopes: frozenset[str]
    trust_tier: TrustTier
    authenticated_via: str
    issued_at: datetime
    expires_at: datetime | None


# -- Scope vocabulary --
# Coarse scope strings for the single-operator model.

SCOPE_RUNS_READ = "runs:read"
SCOPE_RUNS_MANAGE = "runs:manage"
SCOPE_INBOX_READ = "inbox:read"
SCOPE_INBOX_RESPOND = "inbox:respond"
SCOPE_NOTIFICATIONS_READ = "notifications:read"
SCOPE_NOTIFICATIONS_MANAGE = "notifications:manage"
SCOPE_TRACE_READ = "trace:read"
SCOPE_EVENTS_READ = "events:read"
SCOPE_EVENTS_WRITE = "events:write"
SCOPE_CONFIG_READ = "config:read"
SCOPE_VALIDATE_READ = "validate:read"
SCOPE_SOURCES_READ = "sources:read"
SCOPE_SOURCES_MANAGE = "sources:manage"
SCOPE_SUBSCRIPTIONS_READ = "subscriptions:read"
SCOPE_SUBSCRIPTIONS_MANAGE = "subscriptions:manage"
SCOPE_PRINCIPALS_READ = "principals:read"
SCOPE_PRINCIPALS_MANAGE = "principals:manage"

ALL_SCOPES: frozenset[str] = frozenset({
    SCOPE_RUNS_READ,
    SCOPE_RUNS_MANAGE,
    SCOPE_INBOX_READ,
    SCOPE_INBOX_RESPOND,
    SCOPE_NOTIFICATIONS_READ,
    SCOPE_NOTIFICATIONS_MANAGE,
    SCOPE_TRACE_READ,
    SCOPE_EVENTS_READ,
    SCOPE_EVENTS_WRITE,
    SCOPE_CONFIG_READ,
    SCOPE_VALIDATE_READ,
    SCOPE_SOURCES_READ,
    SCOPE_SOURCES_MANAGE,
    SCOPE_SUBSCRIPTIONS_READ,
    SCOPE_SUBSCRIPTIONS_MANAGE,
    SCOPE_PRINCIPALS_READ,
    SCOPE_PRINCIPALS_MANAGE,
})
