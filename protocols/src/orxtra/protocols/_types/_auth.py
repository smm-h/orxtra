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
class Principal:
    id: UUID
    consumer_id: UUID
    scopes: frozenset[str]
    trust_tier: TrustTier
    authenticated_via: str
    issued_at: datetime
    expires_at: datetime | None


# -- Scope vocabulary --
# Coarse scope strings for the single-operator model.

SCOPE_EVENTS_READ = "events:read"
SCOPE_EVENTS_WRITE = "events:write"
SCOPE_SOURCES_MANAGE = "sources:manage"
SCOPE_SUBSCRIPTIONS_MANAGE = "subscriptions:manage"

ALL_SCOPES: frozenset[str] = frozenset({
    SCOPE_EVENTS_READ,
    SCOPE_EVENTS_WRITE,
    SCOPE_SOURCES_MANAGE,
    SCOPE_SUBSCRIPTIONS_MANAGE,
})
