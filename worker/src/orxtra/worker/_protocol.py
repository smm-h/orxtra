"""Wire protocol models for brain-worker communication.

The brain owns all LLM sessions and proxies tool calls to workers
over WebSocket.  Workers execute tools locally and return results.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class ExecuteToolCall(BaseModel):
    """Brain -> Worker: execute a tool with the given arguments."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    call_id: UUID
    tool_name: str
    args: dict[str, Any]


class ToolCallResult(BaseModel):
    """Worker -> Brain: result of a tool execution."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    call_id: UUID
    output: str
    data: Any = None
    mutations: list[str] = []
    duration_ms: float = 0.0
    error: str | None = None


class Heartbeat(BaseModel):
    """Brain -> Worker: heartbeat ping."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    timestamp: float


class HeartbeatAck(BaseModel):
    """Worker -> Brain: heartbeat acknowledgement."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    timestamp: float


class WorkerRegistration(BaseModel):
    """Worker -> Brain: initial registration on connect."""

    model_config = ConfigDict(strict=True, extra="forbid", frozen=True)

    root: str
    capabilities: list[str] = []
