from __future__ import annotations

from decimal import Decimal
from typing import Any

from pydantic import BaseModel, ConfigDict, model_validator


class InlineToolDefinition(BaseModel):
    """A tool definition embedded inline in an agent TOML file.

    Carries the raw dict parsed from [[tools.define]] -- validation
    happens at build time through the shared DataToolDefinition
    schema, not at agent-load time.  This keeps the agent module
    free of data-tool dependencies.
    """

    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    description: str
    namespace: str
    deferred: bool
    tags: list[str] | None = None
    params: dict[str, Any] | None = None
    execution: dict[str, Any]
    output: dict[str, Any] | None = None


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    description: str
    prompt: str
    category: str | None = None
    provider: str | None = None
    model: str | None = None
    allow: list[str]
    deferred: list[str] = []
    budget: Decimal | None = None
    write_paths: list[str] | None = None
    timeout: int | None = None
    inline_tools: list[InlineToolDefinition] = []

    @model_validator(mode="after")
    def _validate_routing(self) -> Agent:
        has_category = self.category is not None
        has_provider = self.provider is not None
        has_model = self.model is not None

        if has_category and (has_provider or has_model):
            msg = (
                "Agent cannot have both 'category' and"
                " 'provider'/'model'. Use one or the other."
            )
            raise ValueError(msg)

        if has_provider != has_model:
            msg = (
                "'provider' and 'model' must both be set"
                " or both be unset"
            )
            raise ValueError(msg)

        if not has_category and not has_provider:
            msg = (
                "Agent must have either 'category' or both"
                " 'provider' and 'model'"
            )
            raise ValueError(msg)

        return self
