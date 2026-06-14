from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    description: str
    prompt: str
    category: str
    allow: list[str]
