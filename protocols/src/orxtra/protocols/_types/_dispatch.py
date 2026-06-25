from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict

from orxtra.protocols._types._actions import Action


class FilterPredicate(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    event_types: list[str] | None = None
    sources: list[str] | None = None
    # Reserved for future jsonb matching.
    data_predicates: dict[str, Any] | None = None


class Subscription(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: UUID
    filter: FilterPredicate
    enabled: bool = True
    storage: str = "persistent"  # "transient" or "persistent"
    owner_run_id: UUID | None = None
    created_at: datetime


class SubscriptionAction(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: UUID
    subscription_id: UUID
    position: int
    action: Action
    accumulator_config: dict[str, Any] | None = None
    created_at: datetime


class Source(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: UUID
    slug: str
    name: str
    auth_method: str | None = None
    auth_config: dict[str, Any] | None = None
    created_at: datetime


class AccumulatorEntry(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    id: UUID
    subscription_action_id: UUID
    event_id: UUID
    created_at: datetime
