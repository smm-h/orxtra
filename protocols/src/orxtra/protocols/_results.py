from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Generic, Protocol, TypeVar, runtime_checkable

T = TypeVar("T")
T_contra = TypeVar("T_contra", contravariant=True)


@dataclass(frozen=True)
class ToolOutput(Generic[T]):
    """Generic wrapper for typed tool results.

    ``data`` holds the structured result; ``text`` holds the
    human/LLM-readable rendering.
    """

    data: T
    text: str


@runtime_checkable
class Renderer(Protocol[T_contra]):
    """Converts a typed result into a text string for the LLM."""

    def render(self, data: T_contra) -> str: ...


# -- Semantic result types --------------------------------------------------


@dataclass(frozen=True)
class FileContent:
    content: str
    is_preview: bool
    total_lines: int
    total_bytes: int


@dataclass(frozen=True)
class DirEntry:
    type: str
    size: int | None
    path: str


@dataclass(frozen=True)
class DirListing:
    entries: list[DirEntry]
    truncated: bool


@dataclass(frozen=True)
class GrepMatch:
    file: str
    line_number: int
    line: str


@dataclass(frozen=True)
class GrepResult:
    matches: list[GrepMatch]
    mode: str
    count: int | None


@dataclass(frozen=True)
class FileStat:
    path: str
    exists: bool
    byte_size: int | None
    line_count: int | None
    language: str | None
    mtime: str | None
    binary: bool


@dataclass(frozen=True)
class DiffResult:
    diff: str
    identical: bool


@dataclass(frozen=True)
class GitOutput:
    output: str
    subcommand: str
    exit_code: int


@dataclass(frozen=True)
class ExecResult:
    stdout: str
    stderr: str
    exit_code: int
    timed_out: bool
    duration_ms: int


@dataclass(frozen=True)
class HttpResponse:
    status_code: int
    headers: dict[str, str]
    body: str
    elapsed_ms: int


@dataclass(frozen=True)
class Confirmation:
    message: str


@dataclass(frozen=True)
class ConsultResponse:
    text: str
    model: str | None


@dataclass(frozen=True)
class TaskLifecycleResult:
    message: str
    task_id: str | None
    details: dict[str, Any] | None
