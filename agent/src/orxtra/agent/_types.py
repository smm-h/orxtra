from __future__ import annotations

from decimal import Decimal

from pydantic import BaseModel, ConfigDict, model_validator


class ExecToolConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    executable: str
    description: str
    timeout_ceiling: int = 300


class ShellConfig(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    allowed_binaries: list[str]
    description: str = "Execute shell commands with whitelisted binaries"
    timeout_ceiling: int = 300


class Agent(BaseModel):
    model_config = ConfigDict(frozen=True, strict=True, extra="forbid")
    name: str
    description: str
    prompt: str
    category: str | None = None
    provider: str | None = None
    model: str | None = None
    allow: list[str]
    budget: Decimal | None = None
    write_paths: list[str] | None = None
    timeout: int | None = None
    exec_tools: list[ExecToolConfig] = []
    shell_config: ShellConfig | None = None

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
