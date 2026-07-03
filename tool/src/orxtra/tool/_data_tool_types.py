"""Pydantic models for data-defined tool TOML definitions.

Strict validation: every model uses ``strict=True, extra='forbid'``.
The ``custom.`` namespace root is enforced at the model level via a
field validator.  Execution type is discriminated via ``type``.
"""

from __future__ import annotations

import re
from typing import Annotated, Any, Literal, Union

from pydantic import BaseModel, ConfigDict, Discriminator, Field, Tag, field_validator


# ---------------------------------------------------------------------------
# Parameter definition
# ---------------------------------------------------------------------------

class ParamDef(BaseModel):
    """A single parameter definition in the ``[params]`` table."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["string", "integer", "number", "boolean"]
    description: str
    required: bool
    pattern: str | None = None

    @field_validator("pattern")
    @classmethod
    def _pattern_compiles(cls, v: str | None) -> str | None:
        if v is not None:
            try:
                re.compile(v)
            except re.error as exc:
                msg = f"Invalid regex pattern: {exc}"
                raise ValueError(msg) from exc
        return v


# ---------------------------------------------------------------------------
# Resource limits (monty)
# ---------------------------------------------------------------------------

class ResourceLimits(BaseModel):
    """Resource limits for the monty execution type."""

    model_config = ConfigDict(strict=True, extra="forbid")

    max_duration_secs: int
    max_allocations: int | None = None
    max_memory: int | None = None


# ---------------------------------------------------------------------------
# Execution variants
# ---------------------------------------------------------------------------

class HttpExecution(BaseModel):
    """``type = "http"`` execution configuration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["http"]
    method: Literal["GET", "HEAD", "POST", "PUT", "DELETE", "PATCH"]
    url: str
    headers: dict[str, str] | None = None
    body_template: str | None = None


class MontyExecution(BaseModel):
    """``type = "monty"`` execution configuration (pydantic-monty)."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["monty"]
    code: str
    capabilities: list[str]
    limits: ResourceLimits


class CommandExecution(BaseModel):
    """``type = "command"`` execution configuration."""

    model_config = ConfigDict(strict=True, extra="forbid")

    type: Literal["command"]
    executable: str
    arg_validation: bool
    timeout_ceiling: int


def _execution_discriminator(v: Any) -> str:  # noqa: ANN401
    """Discriminator for execution type union."""
    if isinstance(v, dict):
        return str(v.get("type", ""))
    return getattr(v, "type", "")


ExecutionConfig = Annotated[
    Union[
        Annotated[HttpExecution, Tag("http")],
        Annotated[MontyExecution, Tag("monty")],
        Annotated[CommandExecution, Tag("command")],
    ],
    Discriminator(_execution_discriminator),
]


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------

class OutputConfig(BaseModel):
    """Output validation configuration."""

    model_config = ConfigDict(
        strict=True, extra="forbid", populate_by_name=True,
    )

    schema_: dict[str, Any] = Field(alias="schema")
    """JSON Schema dict for output validation.

    Named ``schema_`` to avoid shadowing pydantic's ``schema``.
    Mapped from the TOML key ``schema`` via alias.
    """


# ---------------------------------------------------------------------------
# Top-level definition
# ---------------------------------------------------------------------------

class DataToolDefinition(BaseModel):
    """Top-level model for a data-defined tool TOML file.

    Maps to the structure::

        [tool]
        name = "..."
        description = "..."
        namespace = "custom...."
        deferred = false
        tags = ["extra_tag"]

        [params]
        [params.query]
        type = "string"
        ...

        [execution]
        type = "http"
        ...

        [output]
        schema = { ... }
    """

    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str
    namespace: str
    deferred: bool
    tags: list[str] | None = None
    params: dict[str, ParamDef]
    execution: ExecutionConfig
    output: OutputConfig | None = None

    @field_validator("namespace")
    @classmethod
    def _namespace_must_start_with_custom(cls, v: str) -> str:
        if not v.startswith("custom."):
            msg = (
                f"Data-defined tool namespace must start with 'custom.', "
                f"got {v!r}"
            )
            raise ValueError(msg)
        return v

    @field_validator("name")
    @classmethod
    def _name_not_empty(cls, v: str) -> str:
        if not v.strip():
            msg = "Tool name must not be empty"
            raise ValueError(msg)
        return v
