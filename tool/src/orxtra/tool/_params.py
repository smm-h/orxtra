"""Pydantic input models for all tool parameter schemas.

Each model corresponds to a tool's validated input. Models use
``ConfigDict(strict=True, extra="forbid")`` to enforce exact schema
adherence -- no coercion, no extra fields.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


# ---------------------------------------------------------------------------
# Read tools
# ---------------------------------------------------------------------------


class ReadParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    offset: int | None = Field(default=None, ge=1)
    limit: int | None = Field(default=None, ge=1)
    full: bool | None = None


class ListDirParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    recursive: bool | None = None
    pattern: str | None = None
    max_results: int | None = Field(default=None, ge=1)


class GlobParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    pattern: str
    path: str | None = None
    max_results: int | None = Field(default=None, ge=1)


class GrepParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    pattern: str
    path: str | None = None
    case_sensitive: bool | None = None
    context_lines: int | None = Field(default=None, ge=0)
    max_results: int | None = Field(default=None, ge=1)
    include: str | None = None
    mode: str | None = Field(default=None, pattern=r"^(content|files_only|count)$")


class StatParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str


class DiffParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path_a: str
    path_b: str


# ---------------------------------------------------------------------------
# Write tools
# ---------------------------------------------------------------------------


class WriteParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    content: str
    create_dirs: bool | None = None


class EditParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    old_string: str
    new_string: str
    replace_all: bool | None = None


class MultiEditItem(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    file: str
    old_string: str
    new_string: str


class MultiEditParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    edits: list[MultiEditItem] = Field(min_length=1)


class MkdirParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str


class MoveParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source: str
    destination: str


class CopyParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    source: str
    destination: str


class DeleteParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str
    description: str
    recursive: bool


class SetExecutableParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    path: str


# ---------------------------------------------------------------------------
# Notepad tool
# ---------------------------------------------------------------------------


class NotepadParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    type: str = Field(pattern=r"^(learning|decision|issue)$")
    text: str = Field(min_length=1)


# ---------------------------------------------------------------------------
# Git tool
# ---------------------------------------------------------------------------


class GitParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    subcommand: str
    args: list[str] | None = None


# ---------------------------------------------------------------------------
# Task lifecycle tools
# ---------------------------------------------------------------------------


class StartTaskParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task_id: str


class EndTaskParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    message: str


class CreateTaskParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    agent: str
    task_prompt: str
    timeout: int = Field(ge=1)
    context_refinement: bool
    prechecks: list[Any] | None = None
    postchecks: list[Any] | None = None
    variable_values: dict[str, Any] | None = None
    budget: float | None = Field(default=None, ge=0)
    write_paths: list[str] | None = None
    category: str | None = None
    retry: int | None = Field(default=None, ge=0)
    retry_resume: bool | None = None
    retry_inject_failure: bool | None = None
    depends_on: list[str] | None = None


class CreateWorkflowParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    description: str
    goals: list[str] = Field(min_length=1)
    postchecks: list[Any] | None = None
    budget: float | None = Field(default=None, ge=0)


class CreateWaitForParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    name: str
    event_name: str
    timeout: int = Field(ge=1)
    depends_on: list[str] | None = None


class AwaitTaskParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    task_id: str


# ---------------------------------------------------------------------------
# Exec tool (base -- static fields only; dynamic extended at factory time)
# ---------------------------------------------------------------------------


class ExecBaseParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    args: list[str] | None = None
    timeout: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# Shell tool (base -- static fields only; dynamic at factory time)
# ---------------------------------------------------------------------------


class ShellBaseParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    command: str
    timeout: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# HTTP tool (base -- static fields only; method enum set at factory time)
# ---------------------------------------------------------------------------


class HttpBaseParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    method: str
    url: str
    headers: dict[str, str] | None = None
    body: str | None = None
    timeout: int | None = Field(default=None, ge=1)


# ---------------------------------------------------------------------------
# Consult tool
# ---------------------------------------------------------------------------


class ConsultParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")

    agent: str
    question: str = Field(min_length=1)
    variable_values: dict[str, str] | None = None
