"""Tests for the worker sub-project.

Covers protocol serialization, registry enforcement, pipeline split,
bridge send/receive, heartbeat timeout, and idempotent call dedup.
"""

from __future__ import annotations

import json
import time
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from orxtra.worker._brain import (
    BrainWorkerBridge,
    WorkerDisconnectedError,
    _serialize_message,
)
from orxtra.worker._pipeline_split import wrap_tool_for_remote
from orxtra.worker._protocol import (
    ExecuteToolCall,
    Heartbeat,
    HeartbeatAck,
    ToolCallResult,
    WorkerRegistration,
)
from orxtra.worker._registry import WorkerConflictError, WorkerRegistry

# ── 9.1: Protocol serialization roundtrip ──


class TestProtocolSerialization:
    def test_execute_tool_call_roundtrip(self) -> None:
        call_id = uuid4()
        call = ExecuteToolCall(
            call_id=call_id,
            tool_name="read",
            args={"path": "/tmp/test.txt"},
        )
        dumped = call.model_dump_json()
        restored = ExecuteToolCall.model_validate_json(dumped)
        assert restored.call_id == call_id
        assert restored.tool_name == "read"
        assert restored.args == {"path": "/tmp/test.txt"}

    def test_tool_call_result_roundtrip(self) -> None:
        call_id = uuid4()
        result = ToolCallResult(
            call_id=call_id,
            output="file contents",
            data={"lines": 10},
            mutations=["/tmp/out.txt"],
            duration_ms=42.5,
            error=None,
        )
        dumped = result.model_dump_json()
        restored = ToolCallResult.model_validate_json(dumped)
        assert restored.call_id == call_id
        assert restored.output == "file contents"
        assert restored.data == {"lines": 10}
        assert restored.mutations == ["/tmp/out.txt"]
        assert restored.duration_ms == 42.5
        assert restored.error is None

    def test_tool_call_result_with_error(self) -> None:
        result = ToolCallResult(
            call_id=uuid4(),
            output="",
            error="Permission denied",
        )
        assert result.error == "Permission denied"
        assert result.mutations == []
        assert result.duration_ms == 0.0

    def test_heartbeat_roundtrip(self) -> None:
        ts = time.monotonic()
        hb = Heartbeat(timestamp=ts)
        restored = Heartbeat.model_validate_json(hb.model_dump_json())
        assert restored.timestamp == ts

    def test_heartbeat_ack_roundtrip(self) -> None:
        ts = time.monotonic()
        ack = HeartbeatAck(timestamp=ts)
        restored = HeartbeatAck.model_validate_json(ack.model_dump_json())
        assert restored.timestamp == ts

    def test_worker_registration_roundtrip(self) -> None:
        reg = WorkerRegistration(
            root="/home/user/project",
            capabilities=["read", "write", "exec"],
        )
        restored = WorkerRegistration.model_validate_json(
            reg.model_dump_json(),
        )
        assert restored.root == "/home/user/project"
        assert restored.capabilities == ["read", "write", "exec"]

    def test_worker_registration_defaults(self) -> None:
        reg = WorkerRegistration(root="/tmp/work")
        assert reg.capabilities == []

    def test_extra_fields_rejected(self) -> None:
        with pytest.raises(Exception):  # noqa: B017, PT011
            ExecuteToolCall(
                call_id=uuid4(),
                tool_name="read",
                args={},
                extra_field="bad",  # type: ignore[call-arg]
            )


# ── 9.4: WorkerRegistry ──


class TestWorkerRegistry:
    def _make_bridge(self) -> BrainWorkerBridge:
        ws = MagicMock()
        return BrainWorkerBridge(ws=ws, worker_id=uuid4())

    def test_register_and_get(self) -> None:
        registry = WorkerRegistry()
        bridge = self._make_bridge()
        wid = registry.register("consumer-1", "/project/a", ["read"], bridge)
        info = registry.get_worker(wid)
        assert info is not None
        assert info.root == "/project/a"
        assert info.consumer_id == "consumer-1"
        assert info.capabilities == ["read"]

    def test_get_worker_for_root(self) -> None:
        registry = WorkerRegistry()
        bridge = self._make_bridge()
        wid = registry.register("c1", "/project/a", [], bridge)
        info = registry.get_worker_for_root("/project/a")
        assert info is not None
        assert info.id == wid

    def test_get_worker_for_root_not_found(self) -> None:
        registry = WorkerRegistry()
        assert registry.get_worker_for_root("/nonexistent") is None

    def test_one_per_root_enforcement(self) -> None:
        registry = WorkerRegistry()
        bridge1 = self._make_bridge()
        bridge2 = self._make_bridge()
        registry.register("c1", "/project/a", [], bridge1)
        with pytest.raises(WorkerConflictError):
            registry.register("c2", "/project/a", [], bridge2)

    def test_same_consumer_reconnection(self) -> None:
        registry = WorkerRegistry()
        bridge1 = self._make_bridge()
        bridge2 = self._make_bridge()
        wid1 = registry.register("c1", "/project/a", [], bridge1)
        wid2 = registry.register("c1", "/project/a", [], bridge2)
        assert wid1 != wid2
        # Old worker should be gone.
        assert registry.get_worker(wid1) is None
        assert registry.get_worker(wid2) is not None

    def test_unregister(self) -> None:
        registry = WorkerRegistry()
        bridge = self._make_bridge()
        wid = registry.register("c1", "/project/a", [], bridge)
        registry.unregister(wid)
        assert registry.get_worker(wid) is None
        assert registry.get_worker_for_root("/project/a") is None

    def test_assign_task(self) -> None:
        registry = WorkerRegistry()
        bridge = self._make_bridge()
        wid = registry.register("c1", "/project/a", [], bridge)
        task_id = uuid4()
        registry.assign_task(wid, task_id)
        info = registry.get_worker(wid)
        assert info is not None
        assert task_id in info.assigned_tasks

    def test_assign_task_unknown_worker(self) -> None:
        registry = WorkerRegistry()
        with pytest.raises(KeyError):
            registry.assign_task(uuid4(), uuid4())

    def test_unassign_task(self) -> None:
        registry = WorkerRegistry()
        bridge = self._make_bridge()
        wid = registry.register("c1", "/project/a", [], bridge)
        task_id = uuid4()
        registry.assign_task(wid, task_id)
        registry.unassign_task(wid, task_id)
        info = registry.get_worker(wid)
        assert info is not None
        assert task_id not in info.assigned_tasks

    def test_worker_count(self) -> None:
        registry = WorkerRegistry()
        assert registry.worker_count == 0
        bridge = self._make_bridge()
        registry.register("c1", "/a", [], bridge)
        assert registry.worker_count == 1


# ── 9.2: Pipeline split ──


class TestPipelineSplit:
    @pytest.mark.asyncio
    async def test_remote_tool_receives_call_returns_result(self) -> None:
        """Mock worker receives call, returns result, brain completes pipeline."""
        from orxtra.protocols import Tool, ToolOutput

        original_tool = Tool(
            name="read",
            description="Read a file",
            parameters={"type": "object", "properties": {"path": {"type": "string"}}},
            execute=AsyncMock(return_value=ToolOutput(data=None, text="local")),
        )

        worker_result = ToolCallResult(
            call_id=uuid4(),  # will be overridden by actual call
            output="remote file contents",
            data={"lines": 5},
            mutations=[],
            duration_ms=10.0,
        )

        async def mock_send(call: ExecuteToolCall) -> ToolCallResult:
            # Return a result with the matching call_id.
            return ToolCallResult(
                call_id=call.call_id,
                output=worker_result.output,
                data=worker_result.data,
                mutations=worker_result.mutations,
                duration_ms=worker_result.duration_ms,
            )

        scheduler_check = MagicMock(return_value=uuid4())
        trace_calls: list[tuple[str, dict[str, Any], str, int]] = []

        async def trace_cb(
            name: str, args: dict[str, Any], result: str, ms: int,
        ) -> None:
            trace_calls.append((name, args, result, ms))

        mutation_tracker: dict[str, set[str]] = {}

        wrapped = wrap_tool_for_remote(
            tool=original_tool,
            send_to_worker_fn=mock_send,
            secret_registry=None,
            scheduler_check=scheduler_check,
            trace_callback=trace_cb,
            mutation_tracker=mutation_tracker,
            session_id="session-1",
        )

        result = await wrapped.execute({"path": "/test"})

        assert result.text == "remote file contents"
        assert result.data == {"lines": 5}
        scheduler_check.assert_called_once_with("session-1")
        assert len(trace_calls) == 1
        assert trace_calls[0][0] == "read"

    @pytest.mark.asyncio
    async def test_remote_tool_tracks_mutations(self) -> None:
        from orxtra.protocols import Tool, ToolOutput

        original_tool = Tool(
            name="write",
            description="Write a file",
            parameters={"type": "object", "properties": {}},
            execute=AsyncMock(return_value=ToolOutput(data=None, text="")),
        )

        async def mock_send(call: ExecuteToolCall) -> ToolCallResult:
            return ToolCallResult(
                call_id=call.call_id,
                output="ok",
                mutations=["/project/file.txt"],
                duration_ms=5.0,
            )

        mutation_tracker: dict[str, set[str]] = {}

        wrapped = wrap_tool_for_remote(
            tool=original_tool,
            send_to_worker_fn=mock_send,
            secret_registry=None,
            scheduler_check=MagicMock(return_value=uuid4()),
            trace_callback=None,
            mutation_tracker=mutation_tracker,
            session_id="s1",
        )

        await wrapped.execute({})

        assert "/project/file.txt" in mutation_tracker["s1"]

    @pytest.mark.asyncio
    async def test_start_task_skips_scheduler_check(self) -> None:
        from orxtra.protocols import Tool, ToolOutput

        original_tool = Tool(
            name="start_task",
            description="Start a task",
            parameters={"type": "object", "properties": {}},
            execute=AsyncMock(return_value=ToolOutput(data=None, text="")),
        )

        async def mock_send(call: ExecuteToolCall) -> ToolCallResult:
            return ToolCallResult(
                call_id=call.call_id,
                output="started",
                duration_ms=1.0,
            )

        scheduler_check = MagicMock(return_value=uuid4())

        wrapped = wrap_tool_for_remote(
            tool=original_tool,
            send_to_worker_fn=mock_send,
            secret_registry=None,
            scheduler_check=scheduler_check,
            trace_callback=None,
            mutation_tracker=None,
            session_id="s1",
            is_start_task=True,
        )

        await wrapped.execute({})

        scheduler_check.assert_not_called()


# ── 9.3: BrainWorkerBridge send/receive ──


def _make_fake_ws() -> MagicMock:
    """Create a mock WebSocket with send_text and receive_text."""
    ws = MagicMock()
    ws.send_text = AsyncMock()
    ws.receive_text = AsyncMock()
    return ws


class TestBrainWorkerBridge:
    @pytest.mark.asyncio
    async def test_send_and_receive(self) -> None:
        ws = _make_fake_ws()
        worker_id = uuid4()
        bridge = BrainWorkerBridge(ws=ws, worker_id=worker_id)

        call_id = uuid4()
        call = ExecuteToolCall(
            call_id=call_id,
            tool_name="read",
            args={"path": "/test"},
        )

        # Build the response envelope the worker would send back.
        result_data = ToolCallResult(
            call_id=call_id,
            output="contents",
            duration_ms=5.0,
        )
        response_envelope = json.dumps({
            "type": "tool_call_result",
            "data": json.loads(result_data.model_dump_json()),
        })

        # Make ws.send_text trigger response dispatch (simulates
        # the worker processing the call and sending back a result).
        original_send = ws.send_text

        async def send_and_respond(payload: str) -> None:
            await original_send(payload)
            # Simulate the worker responding immediately.
            bridge._dispatch_message(response_envelope)

        ws.send_text = send_and_respond

        result = await bridge.send_tool_call(call)

        assert result.call_id == call_id
        assert result.output == "contents"

    @pytest.mark.asyncio
    async def test_disconnected_raises(self) -> None:
        ws = _make_fake_ws()
        bridge = BrainWorkerBridge(ws=ws, worker_id=uuid4())
        bridge._connected = False

        call = ExecuteToolCall(
            call_id=uuid4(),
            tool_name="read",
            args={},
        )

        with pytest.raises(WorkerDisconnectedError):
            await bridge.send_tool_call(call)

    @pytest.mark.asyncio
    async def test_idempotent_cached_result(self) -> None:
        ws = _make_fake_ws()
        bridge = BrainWorkerBridge(ws=ws, worker_id=uuid4())

        call_id = uuid4()
        cached_result = ToolCallResult(
            call_id=call_id,
            output="cached",
            duration_ms=1.0,
        )
        bridge._result_cache[call_id] = cached_result

        call = ExecuteToolCall(
            call_id=call_id,
            tool_name="read",
            args={},
        )

        result = await bridge.send_tool_call(call)
        assert result.output == "cached"
        # Should not have sent anything to the WebSocket.
        ws.send_text.assert_not_called()


# ── 9.6: Heartbeat timeout ──


class TestHeartbeatTimeout:
    @pytest.mark.asyncio
    async def test_heartbeat_ack_updates_timestamp(self) -> None:
        ws = _make_fake_ws()
        bridge = BrainWorkerBridge(ws=ws, worker_id=uuid4())

        now = time.monotonic()
        ack_msg = json.dumps({
            "type": "heartbeat_ack",
            "data": {"timestamp": now},
        })
        bridge._dispatch_message(ack_msg)
        assert bridge._last_heartbeat_ack == now

    def test_dispatch_unknown_type_logs_warning(self) -> None:
        ws = _make_fake_ws()
        bridge = BrainWorkerBridge(ws=ws, worker_id=uuid4())

        # Should not raise -- just log a warning.
        bridge._dispatch_message(json.dumps({
            "type": "unknown_type",
            "data": {},
        }))

    def test_dispatch_invalid_json(self) -> None:
        ws = _make_fake_ws()
        bridge = BrainWorkerBridge(ws=ws, worker_id=uuid4())

        # Should not raise -- just log a warning.
        bridge._dispatch_message("not json at all")


# ── 9.6: Idempotent call_id dedup ──


class TestIdempotentDedup:
    @pytest.mark.asyncio
    async def test_duplicate_result_dispatch_caches(self) -> None:
        """When a result arrives for a call_id with no pending future,
        it should still be cached for idempotency."""
        ws = _make_fake_ws()
        bridge = BrainWorkerBridge(ws=ws, worker_id=uuid4())

        call_id = uuid4()
        result = ToolCallResult(
            call_id=call_id,
            output="result",
            duration_ms=1.0,
        )

        # No pending future for this call_id.
        response = json.dumps({
            "type": "tool_call_result",
            "data": json.loads(result.model_dump_json()),
        })
        bridge._dispatch_message(response)

        # Should be cached.
        assert call_id in bridge._result_cache
        assert bridge._result_cache[call_id].output == "result"


# ── Serialize helper ──


class TestSerializeMessage:
    def test_envelope_structure(self) -> None:
        hb = Heartbeat(timestamp=123.456)
        raw = _serialize_message("heartbeat", hb)
        parsed = json.loads(raw)
        assert parsed["type"] == "heartbeat"
        assert parsed["data"]["timestamp"] == 123.456


# ── TaskSpec remote field ──


class TestTaskSpecRemoteField:
    def test_default_false(self) -> None:
        from orxtra.protocols import TaskSpec

        spec = TaskSpec(name="test")
        assert spec.remote is False

    def test_set_true(self) -> None:
        from orxtra.protocols import TaskSpec

        spec = TaskSpec(name="test", remote=True)
        assert spec.remote is True
