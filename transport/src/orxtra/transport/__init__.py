from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("orxtra-transport")
except PackageNotFoundError:
    __version__ = "0.0.0"

from orxtra.transport._events import (
    ApiRetry,
    ContentBlock,
    ContextWarning,
    Error,
    Event,
    LivenessWarning,
    RateLimit,
    Result,
    SessionSuspended,
    StepFinish,
    StepStart,
    StreamDelta,
    StreamToolUse,
    StreamUsage,
    StuckDetected,
    Text,
    Thinking,
    ToolExecuting,
    ToolUse,
    UnknownEvent,
    Usage,
)
from orxtra.transport._liveness import LivenessMonitor
from orxtra.transport._provider import Provider, RetryPolicy
from orxtra.transport._state_machine import Continuation, TransportState
from orxtra.transport._transport import Transport

__all__ = [
    "__version__",
    "ApiRetry",
    "ContentBlock",
    "ContextWarning",
    "Continuation",
    "Error",
    "Event",
    "LivenessMonitor",
    "LivenessWarning",
    "Provider",
    "RateLimit",
    "Result",
    "RetryPolicy",
    "SessionSuspended",
    "StepFinish",
    "StepStart",
    "StreamDelta",
    "StreamToolUse",
    "StreamUsage",
    "StuckDetected",
    "Text",
    "Thinking",
    "ToolExecuting",
    "ToolUse",
    "Transport",
    "TransportState",
    "UnknownEvent",
    "Usage",
]
