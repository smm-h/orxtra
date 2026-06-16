from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from orxt.transport._state_machine import Continuation


@dataclass(frozen=True)
class ContentBlock:
    type: str
    text: str | None = None
    tool_use_id: str | None = None
    tool_name: str | None = None
    tool_input: dict[str, Any] | None = None


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class StepStart:
    session_id: str


@dataclass(frozen=True)
class Text:
    text: str


@dataclass(frozen=True)
class StreamDelta:
    text: str


@dataclass(frozen=True)
class Thinking:
    text: str


@dataclass(frozen=True)
class ToolUse:
    tool_name: str
    input: dict[str, Any]
    output: str
    status: str
    error: str | None = None
    duration_ms: int = 0


@dataclass(frozen=True)
class StepFinish:
    reason: str
    input_tokens: int = 0
    output_tokens: int = 0
    reasoning_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass(frozen=True)
class ApiRetry:
    attempt: int
    max_retries: int
    delay_ms: int
    status_code: int
    error: str


@dataclass(frozen=True)
class Error:
    name: str
    message: str
    metadata: dict[str, Any] | None = None


@dataclass(frozen=True)
class Result:
    text: str
    session_id: str
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    total_reasoning_tokens: int = 0
    total_cache_read_tokens: int = 0
    total_cache_write_tokens: int = 0
    tool_calls: int = 0


@dataclass(frozen=True)
class SessionSuspended:
    continuation: Continuation
    session_id: str | None


Event = (
    StepStart
    | Text
    | StreamDelta
    | Thinking
    | ToolUse
    | StepFinish
    | ApiRetry
    | Error
    | Result
    | SessionSuspended
)
