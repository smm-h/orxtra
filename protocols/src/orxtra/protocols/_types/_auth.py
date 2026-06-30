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


@dataclass(frozen=True)
class Principal:
    id: UUID
    consumer_id: UUID
    scopes: frozenset[str]
    trust_tier: TrustTier
    authenticated_via: str
    issued_at: datetime
    expires_at: datetime | None
