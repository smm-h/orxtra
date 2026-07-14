"""E2E proof of the worker routing and bridge pipeline.

Verifies the worker subsystem's routing decisions and remote tool
wrapping without requiring a live scheduler:

1. ``should_route_to_worker`` returns True for ANYWHERE tools with a
   target and False for LOCAL tools or no target.
2. ``wrap_tool_for_remote`` produces a tool that routes through a mock
   BrainWorkerBridge, executing remotely and returning the result.
3. WorkerRegistry tracks connected workers and enforces one-per-root.
4. Disconnecting a worker (bridge.connected=False) means
   ``get_worker_for_root`` still returns the info, but the bridge
   is no longer usable for tool calls.

These tests are NOT PG-gated -- they exercise pure in-process logic
(the routing decision is a pure function; the bridge/registry are
in-memory). PG-gated tests exist in tests/test_dispatch_worker.py
for the full dispatch worker lifecycle.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest
from orxtra.protocols import Tool, ToolLocation, ToolOutput
from orxtra.worker._brain import (
    BrainWorkerBridge,
    WorkerDisconnectedError,
)
from orxtra.worker._pipeline_split import (
    should_route_to_worker,
    wrap_tool_for_remote,
)
from orxtra.worker._protocol import ExecuteToolCall, ToolCallResult
from orxtra.worker._registry import WorkerConflictError, WorkerRegistry

# ---------------------------------------------------------------------------
# 1. should_route_to_worker: pure routing decision
# ---------------------------------------------------------------------------


class TestRoutingDecision:
    """Verify should_route_to_worker produces the correct routing answer."""

    def test_anywhere_with_target_routes_to_worker(self) -> None:
        assert should_route_to_worker(ToolLocation.ANYWHERE, "test-root") is True

    def test_local_with_target_stays_local(self) -> None:
        assert should_route_to_worker(ToolLocation.LOCAL, "test-root") is False

    def test_anywhere_without_target_stays_local(self) -> None:
        assert should_route_to_worker(ToolLocation.ANYWHERE, None) is False

    def test_local_without_target_stays_local(self) -> None:
        assert should_route_to_worker(ToolLocation.LOCAL, None) is False


# ---------------------------------------------------------------------------
# 2. wrap_tool_for_remote: tool wrapping produces correct pipeline
# ---------------------------------------------------------------------------


def _noop_scheduler_check(session_id: str) -> UUID:
    """Stub scheduler check that always succeeds."""
    return uuid4()


async def _make_mock_tool() -> Tool:
    """Create a test tool with ANYWHERE location."""
    async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
        return ToolOutput(data=args, text="local result")

    return Tool(
        name="test_read",
        description="A test read tool",
        parameters={"type": "object", "properties": {"path": {"type": "string"}}},
        execute=execute,
        location=ToolLocation.ANYWHERE,
    )


class TestWrapToolForRemote:
    """Verify wrap_tool_for_remote correctly routes tool calls."""

    async def test_wrapped_tool_calls_worker(self) -> None:
        """A wrapped tool sends the call to the worker and returns its result."""
        tool = await _make_mock_tool()
        worker_calls: list[ExecuteToolCall] = []

        async def mock_send(call: ExecuteToolCall) -> ToolCallResult:
            worker_calls.append(call)
            return ToolCallResult(
                call_id=call.call_id,
                output="remote result: " + str(call.args),
                data=call.args,
                error=None,
                mutations=[],
                duration_ms=42,
            )

        wrapped = wrap_tool_for_remote(
            tool=tool,
            send_to_worker_fn=mock_send,
            secret_registry=None,
            scheduler_check=_noop_scheduler_check,
            trace_callback=None,
            mutation_tracker=None,
            session_id="test-session",
        )

        # The wrapped tool preserves the schema.
        assert wrapped.name == "test_read"
        assert wrapped.location == ToolLocation.ANYWHERE

        # Execute the wrapped tool.
        result = await wrapped.execute({"path": "/tmp/test"})

        # The call went through mock_send.
        assert len(worker_calls) == 1
        assert worker_calls[0].tool_name == "test_read"
        assert worker_calls[0].args == {"path": "/tmp/test"}

        # The result came from the worker.
        assert "remote result" in result.text

    async def test_wrapped_tool_tracks_mutations(self) -> None:
        """Mutations reported by the worker are tracked in mutation_tracker."""
        tool = await _make_mock_tool()
        tracker: dict[str, set[str]] = {}

        async def mock_send(call: ExecuteToolCall) -> ToolCallResult:
            return ToolCallResult(
                call_id=call.call_id,
                output="ok",
                data=None,
                error=None,
                mutations=["/tmp/mutated.txt"],
                duration_ms=10,
            )

        wrapped = wrap_tool_for_remote(
            tool=tool,
            send_to_worker_fn=mock_send,
            secret_registry=None,
            scheduler_check=_noop_scheduler_check,
            trace_callback=None,
            mutation_tracker=tracker,
            session_id="sess-1",
        )

        await wrapped.execute({"path": "/tmp/test"})

        assert "sess-1" in tracker
        assert "/tmp/mutated.txt" in tracker["sess-1"]


# ---------------------------------------------------------------------------
# 3. WorkerRegistry: registration and conflict detection
# ---------------------------------------------------------------------------


class _FakeBridge:
    """Minimal stand-in for BrainWorkerBridge in registry tests."""

    def __init__(self, *, connected: bool = True) -> None:
        self.connected = connected


class TestWorkerRegistry:
    def test_register_and_lookup(self) -> None:
        registry = WorkerRegistry()
        bridge = _FakeBridge()
        worker_id = registry.register(
            consumer_id=uuid4(),
            root="/home/test/project",
            capabilities=[],
            bridge=bridge,  # type: ignore[arg-type]
        )

        info = registry.get_worker(worker_id)
        assert info is not None
        assert info.root == "/home/test/project"

        by_root = registry.get_worker_for_root("/home/test/project")
        assert by_root is not None
        assert by_root.id == worker_id

    def test_conflict_on_same_root(self) -> None:
        registry = WorkerRegistry()
        bridge1 = _FakeBridge()
        bridge2 = _FakeBridge()
        consumer1 = uuid4()
        consumer2 = uuid4()

        registry.register(
            consumer_id=consumer1,
            root="/shared/root",
            capabilities=[],
            bridge=bridge1,  # type: ignore[arg-type]
        )

        with pytest.raises(WorkerConflictError):
            registry.register(
                consumer_id=consumer2,
                root="/shared/root",
                capabilities=[],
                bridge=bridge2,  # type: ignore[arg-type]
            )

    def test_same_consumer_can_reconnect(self) -> None:
        registry = WorkerRegistry()
        consumer = uuid4()
        bridge1 = _FakeBridge()
        bridge2 = _FakeBridge()

        w1 = registry.register(
            consumer_id=consumer,
            root="/home/test",
            capabilities=[],
            bridge=bridge1,  # type: ignore[arg-type]
        )

        # Same consumer re-registers -- old entry is replaced.
        w2 = registry.register(
            consumer_id=consumer,
            root="/home/test",
            capabilities=[],
            bridge=bridge2,  # type: ignore[arg-type]
        )

        assert w1 != w2
        assert registry.get_worker(w1) is None
        assert registry.get_worker(w2) is not None
        assert registry.worker_count == 1

    def test_unregister_cleans_up(self) -> None:
        registry = WorkerRegistry()
        bridge = _FakeBridge()
        wid = registry.register(
            consumer_id=uuid4(),
            root="/cleanup",
            capabilities=[],
            bridge=bridge,  # type: ignore[arg-type]
        )

        registry.unregister(wid)
        assert registry.get_worker(wid) is None
        assert registry.get_worker_for_root("/cleanup") is None
        assert registry.worker_count == 0

    def test_task_assignment(self) -> None:
        registry = WorkerRegistry()
        bridge = _FakeBridge()
        wid = registry.register(
            consumer_id=uuid4(),
            root="/tasks",
            capabilities=[],
            bridge=bridge,  # type: ignore[arg-type]
        )

        task_id = uuid4()
        registry.assign_task(wid, task_id)
        info = registry.get_worker(wid)
        assert info is not None
        assert task_id in info.assigned_tasks

        registry.unassign_task(wid, task_id)
        assert task_id not in info.assigned_tasks


# ---------------------------------------------------------------------------
# 4. Bridge disconnection: get_worker_bridge returns None pattern
# ---------------------------------------------------------------------------


class TestBridgeDisconnection:
    """Verify that a disconnected bridge is detectable by callers."""

    async def test_disconnected_bridge_rejects_tool_calls(self) -> None:
        """A disconnected BrainWorkerBridge raises WorkerDisconnectedError."""
        # Create a mock WebSocket that we can mark disconnected.
        ws = AsyncMock()
        bridge = BrainWorkerBridge(ws, uuid4())
        # Mark it disconnected without starting the loops.
        bridge._connected = False

        call = ExecuteToolCall(
            call_id=uuid4(),
            tool_name="read",
            args={"path": "/tmp/test"},
        )

        with pytest.raises(WorkerDisconnectedError):
            await bridge.send_tool_call(call)

    def test_get_worker_for_root_returns_none_after_unregister(self) -> None:
        """After unregistering a worker, get_worker_for_root returns None."""
        registry = WorkerRegistry()
        bridge = _FakeBridge()
        wid = registry.register(
            consumer_id=uuid4(),
            root="/disconnect-test",
            capabilities=[],
            bridge=bridge,  # type: ignore[arg-type]
        )

        # Worker is registered: lookup succeeds.
        assert registry.get_worker_for_root("/disconnect-test") is not None

        # Unregister (simulates disconnect handling).
        registry.unregister(wid)

        # Worker is gone: lookup returns None.
        assert registry.get_worker_for_root("/disconnect-test") is None

        # A subsequent targeted task would need to hard-error; the caller
        # checks get_worker_for_root and raises if None.
