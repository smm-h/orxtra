from __future__ import annotations

from pydantic import BaseModel, ConfigDict


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
    category: str
    allow: list[str]
    exec_tools: list[ExecToolConfig] = []
    shell_config: ShellConfig | None = None
