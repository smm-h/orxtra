"""Brain-worker bridge: manages a single worker WebSocket connection.

The brain side of the protocol.  Sends ExecuteToolCall messages to
the worker, awaits ToolCallResult responses.  Handles heartbeat
ping/pong and tracks worker connection state.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from typing import TYPE_CHECKING, Any
from uuid import UUID

from orxtra.worker._protocol import (
    ExecuteToolCall,
    Heartbeat,
    HeartbeatAck,
    ToolCallResult,
)
from pydantic import ValidationError

if TYPE_CHECKING:
    from fastware import WebSocket

_logger = logging.getLogger("orxtra.worker.brain")

_HEARTBEAT_INTERVAL_S = 30.0
_HEARTBEAT_TIMEOUT_S = 10.0


class WorkerDisconnectedError(Exception):
    """Raised when a tool call is attempted on a disconnected worker."""


class ToolCallTimeoutError(Exception):
    """Raised when a worker does not respond to a tool call within the timeout."""


class BrainWorkerBridge:
    """Manages the WebSocket connection to a single worker.

    Thread-safe for concurrent tool calls: each call gets a unique
    call_id and its own Future.  The receive loop dispatches responses
    to the correct Future.
    """

    def __init__(self, ws: WebSocket, worker_id: UUID) -> None:
        self._ws = ws
        self._worker_id = worker_id
        self._connected = True
        self._pending: dict[UUID, asyncio.Future[ToolCallResult]] = {}
        self._result_cache: dict[UUID, ToolCallResult] = {}
        self._receive_task: asyncio.Task[None] | None = None
        self._heartbeat_task: asyncio.Task[None] | None = None
        self._last_heartbeat_ack: float = time.monotonic()

    @property
    def connected(self) -> bool:
        return self._connected

    @property
    def worker_id(self) -> UUID:
        return self._worker_id

    def start(self) -> None:
        """Start the receive and heartbeat loops."""
        self._receive_task = asyncio.create_task(
            self._receive_loop(),
            name=f"worker-{self._worker_id}-recv",
        )
        self._heartbeat_task = asyncio.create_task(
            self._heartbeat_loop(),
            name=f"worker-{self._worker_id}-hb",
        )

    async def stop(self) -> None:
        """Stop the receive and heartbeat loops."""
        self._connected = False
        if self._heartbeat_task is not None:
            self._heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._heartbeat_task
        if self._receive_task is not None:
            self._receive_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._receive_task
        # Fail any pending calls.
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    WorkerDisconnectedError("Worker disconnected"),
                )
        self._pending.clear()

    async def send_tool_call(
        self,
        call: ExecuteToolCall,
        timeout: float | None = None,  # noqa: ASYNC109 -- per-call timeout is the API
    ) -> ToolCallResult:
        """Send a tool call to the worker and await the result.

        Idempotent: if a result for this call_id is already cached,
        returns the cached result without re-sending.
        """
        if not self._connected:
            msg = f"Worker {self._worker_id} is disconnected"
            raise WorkerDisconnectedError(msg)

        # Idempotent: check cache first.
        cached = self._result_cache.get(call.call_id)
        if cached is not None:
            return cached

        loop = asyncio.get_running_loop()
        future: asyncio.Future[ToolCallResult] = loop.create_future()
        self._pending[call.call_id] = future

        try:
            payload = _serialize_message("execute_tool_call", call)
            await self._ws.send_text(payload)

            if timeout is not None:
                result = await asyncio.wait_for(future, timeout=timeout)
            else:
                result = await future
        except TimeoutError as exc:
            self._pending.pop(call.call_id, None)
            msg = f"Tool call {call.call_id} timed out after {timeout}s"
            raise ToolCallTimeoutError(msg) from exc
        except Exception:
            self._pending.pop(call.call_id, None)
            raise
        else:
            # Cache for idempotency.
            self._result_cache[call.call_id] = result
            return result

    async def _receive_loop(self) -> None:
        """Continuously receive messages from the worker WebSocket."""
        from fastware import WebSocketDisconnect

        try:
            while self._connected:
                raw = await self._ws.receive_text()
                self._dispatch_message(raw)
        except WebSocketDisconnect:
            _logger.info("Worker %s disconnected", self._worker_id)
        except asyncio.CancelledError:
            raise
        except Exception:
            _logger.exception(
                "Error in receive loop for worker %s",
                self._worker_id,
            )
        finally:
            self._mark_disconnected()

    def _dispatch_message(self, raw: str) -> None:
        """Parse and dispatch a single message from the worker."""
        try:
            envelope: dict[str, Any] = json.loads(raw)
        except json.JSONDecodeError:
            _logger.warning(
                "Invalid JSON from worker %s", self._worker_id,
            )
            return

        msg_type = envelope.get("type")

        if msg_type == "tool_call_result":
            data = envelope.get("data", {})
            try:
                # Use model_validate_json for strict UUID handling:
                # wire data has UUIDs as strings, strict mode requires
                # UUID objects.  model_validate_json coerces correctly.
                result = ToolCallResult.model_validate_json(json.dumps(data))
            except Exception as exc:  # noqa: BLE001 -- malformed worker data must not kill the loop
                _logger.warning(
                    "Invalid ToolCallResult from worker %s: %s",
                    self._worker_id,
                    exc,
                )
                return
            future = self._pending.pop(result.call_id, None)
            if future is not None and not future.done():
                future.set_result(result)
            else:
                # Cache anyway for idempotency.
                self._result_cache[result.call_id] = result

        elif msg_type == "heartbeat_ack":
            data = envelope.get("data", {})
            try:
                ack = HeartbeatAck.model_validate_json(json.dumps(data))
            except ValidationError:
                return
            self._last_heartbeat_ack = ack.timestamp

        else:
            _logger.warning(
                "Unknown message type '%s' from worker %s",
                msg_type,
                self._worker_id,
            )

    async def _heartbeat_loop(self) -> None:
        """Send heartbeats and detect timeouts."""
        while self._connected:
            await asyncio.sleep(_HEARTBEAT_INTERVAL_S)
            if not self._connected:
                break

            now = time.monotonic()
            hb = Heartbeat(timestamp=now)
            payload = _serialize_message("heartbeat", hb)
            try:
                await self._ws.send_text(payload)
            except Exception:  # noqa: BLE001 -- any send failure means disconnect
                _logger.warning(
                    "Failed to send heartbeat to worker %s",
                    self._worker_id,
                )
                self._mark_disconnected()
                break

            # Wait for ack.
            await asyncio.sleep(_HEARTBEAT_TIMEOUT_S)
            if (
                self._connected
                and self._last_heartbeat_ack < now
            ):
                _logger.warning(
                    "Heartbeat timeout for worker %s",
                    self._worker_id,
                )
                self._mark_disconnected()
                break

    def _mark_disconnected(self) -> None:
        """Mark the worker as disconnected and fail pending calls."""
        if not self._connected:
            return
        self._connected = False
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(
                    WorkerDisconnectedError("Worker disconnected"),
                )
        self._pending.clear()


def _serialize_message(msg_type: str, model: Any) -> str:
    """Serialize a protocol message as a JSON envelope."""
    return json.dumps({
        "type": msg_type,
        "data": json.loads(model.model_dump_json()),
    })
