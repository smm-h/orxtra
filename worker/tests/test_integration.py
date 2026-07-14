"""Integration tests for native and Docker workers.

Tests verify the key integration points between workers and a mock brain:
protocol roundtrips, write safety, heartbeat handling, and Docker container
lifecycle.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import shutil
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from orxtra.protocols import ToolCapability
from orxtra.worker._docker import DockerNotFoundError, DockerWorker
from orxtra.worker._native import NativeWorker, build_worker_tools
from orxtra.worker._protocol import (
    ExecuteToolCall,
    Heartbeat,
    HeartbeatAck,
    ToolCallResult,
    WorkerRegistration,
)
from orxtra.write_safety import StaleWriteTracker, WriteQueue
from websockets.asyncio.server import serve as ws_serve


def _serialize(msg_type: str, model: Any) -> str:
    """Serialize a protocol message as a JSON envelope."""
    return json.dumps({
        "type": msg_type,
        "data": json.loads(model.model_dump_json()),
    })


# -- 10.3.1: build_worker_tools --


class TestBuildWorkerTools:
    def test_builds_essential_tools(self, tmp_path: Path) -> None:
        queue = WriteQueue()
        tracker = StaleWriteTracker()
        tools = build_worker_tools(
            root=tmp_path,
            queue=queue,
            tracker=tracker,
            session_id="test",
        )
        # Must have the core tools.
        expected = {
            "read", "write", "edit", "multi_edit", "grep", "glob",
            "stat", "diff", "list_dir", "mkdir", "move", "copy",
            "delete", "set_executable", "git",
        }
        for name in expected:
            assert name in tools, f"Missing tool: {name}"

    def test_exec_tools_present(self, tmp_path: Path) -> None:
        queue = WriteQueue()
        tracker = StaleWriteTracker()
        tools = build_worker_tools(
            root=tmp_path,
            queue=queue,
            tracker=tracker,
            session_id="test",
        )
        assert "pytest" in tools
        assert "uv" in tools


# -- 10.3.2: Protocol roundtrip test --


class TestProtocolRoundtrip:
    @pytest.mark.asyncio
    async def test_worker_executes_read_tool(self, tmp_path: Path) -> None:
        """Create a mock brain WS server, connect a NativeWorker,
        send a read tool call, verify the result."""
        # Create a test file.
        test_file = tmp_path / "hello.txt"
        test_file.write_text("hello world\n", encoding="utf-8")

        results: list[ToolCallResult] = []
        registration: list[WorkerRegistration] = []
        worker_connected = asyncio.Event()
        result_received = asyncio.Event()

        async def mock_brain(ws: Any) -> None:
            # Wait for registration message.
            raw = await ws.recv()
            envelope = json.loads(raw)
            assert envelope["type"] == "worker_registration"
            reg = WorkerRegistration.model_validate_json(
                json.dumps(envelope["data"]),
            )
            registration.append(reg)
            worker_connected.set()

            # Send a read tool call.
            call = ExecuteToolCall(
                call_id=uuid4(),
                tool_name="read",
                args={"path": str(test_file)},
            )
            await ws.send(_serialize("execute_tool_call", call))

            # Wait for result.
            raw = await ws.recv()
            envelope = json.loads(raw)
            assert envelope["type"] == "tool_call_result"
            result = ToolCallResult.model_validate_json(
                json.dumps(envelope["data"]),
            )
            results.append(result)
            result_received.set()

            # Close cleanly.
            await ws.close()

        async with ws_serve(mock_brain, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            worker = NativeWorker(
                brain_url=url,
                root=tmp_path,
                api_key="test-key",
                capabilities=[ToolCapability.READ, ToolCapability.WRITE],
            )

            # Run worker in background; it will exit when the server
            # closes the connection.
            worker_task = asyncio.create_task(worker.run())

            # Wait for result with timeout.
            await asyncio.wait_for(result_received.wait(), timeout=10.0)

            # Cancel the worker if still running.
            worker.stop()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        assert len(registration) == 1
        assert registration[0].root == str(tmp_path.resolve())
        assert registration[0].capabilities == [ToolCapability.READ, ToolCapability.WRITE]

        assert len(results) == 1
        assert results[0].error is None
        assert "hello world" in results[0].output
        assert results[0].duration_ms > 0


# -- 10.3.3: Write safety test --


class TestWriteSafety:
    @pytest.mark.asyncio
    async def test_write_tool_uses_write_queue(self, tmp_path: Path) -> None:
        """Worker write tool should succeed on first call."""
        results: list[ToolCallResult] = []
        result_received = asyncio.Event()

        async def mock_brain(ws: Any) -> None:
            # Consume registration.
            await ws.recv()

            # Send a write tool call.
            call = ExecuteToolCall(
                call_id=uuid4(),
                tool_name="write",
                args={
                    "path": str(tmp_path / "output.txt"),
                    "content": "test content",
                },
            )
            await ws.send(_serialize("execute_tool_call", call))

            # Wait for result.
            raw = await ws.recv()
            envelope = json.loads(raw)
            result = ToolCallResult.model_validate_json(
                json.dumps(envelope["data"]),
            )
            results.append(result)
            result_received.set()
            await ws.close()

        async with ws_serve(mock_brain, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            worker = NativeWorker(
                brain_url=url,
                root=tmp_path,
                api_key="test-key",
            )
            worker_task = asyncio.create_task(worker.run())
            await asyncio.wait_for(result_received.wait(), timeout=10.0)

            worker.stop()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        assert len(results) == 1
        assert results[0].error is None
        # Verify the file was actually written.
        written = (tmp_path / "output.txt").read_text(encoding="utf-8")
        assert written == "test content"
        # Write tool should track mutations.
        assert str(tmp_path / "output.txt") in results[0].mutations


# -- 10.3.4: Heartbeat test --


class TestHeartbeat:
    @pytest.mark.asyncio
    async def test_worker_responds_to_heartbeat(self, tmp_path: Path) -> None:
        """Worker should respond to Heartbeat with HeartbeatAck."""
        ack_received = asyncio.Event()
        acks: list[HeartbeatAck] = []

        async def mock_brain(ws: Any) -> None:
            # Consume registration.
            await ws.recv()

            # Send heartbeat.
            hb = Heartbeat(timestamp=12345.678)
            await ws.send(_serialize("heartbeat", hb))

            # Wait for ack.
            raw = await ws.recv()
            envelope = json.loads(raw)
            assert envelope["type"] == "heartbeat_ack"
            ack = HeartbeatAck.model_validate(envelope["data"])
            acks.append(ack)
            ack_received.set()
            await ws.close()

        async with ws_serve(mock_brain, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            worker = NativeWorker(
                brain_url=url,
                root=tmp_path,
                api_key="test-key",
            )
            worker_task = asyncio.create_task(worker.run())
            await asyncio.wait_for(ack_received.wait(), timeout=10.0)

            worker.stop()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        assert len(acks) == 1
        assert acks[0].timestamp == 12345.678


# -- 10.3.5: Unknown tool test --


class TestUnknownTool:
    @pytest.mark.asyncio
    async def test_unknown_tool_returns_error(self, tmp_path: Path) -> None:
        """Worker should return an error for unknown tool names."""
        results: list[ToolCallResult] = []
        result_received = asyncio.Event()

        async def mock_brain(ws: Any) -> None:
            await ws.recv()  # registration

            call = ExecuteToolCall(
                call_id=uuid4(),
                tool_name="nonexistent_tool",
                args={},
            )
            await ws.send(_serialize("execute_tool_call", call))

            raw = await ws.recv()
            envelope = json.loads(raw)
            result = ToolCallResult.model_validate_json(
                json.dumps(envelope["data"]),
            )
            results.append(result)
            result_received.set()
            await ws.close()

        async with ws_serve(mock_brain, "127.0.0.1", 0) as server:
            port = server.sockets[0].getsockname()[1]
            url = f"ws://127.0.0.1:{port}"

            worker = NativeWorker(
                brain_url=url,
                root=tmp_path,
                api_key="test-key",
            )
            worker_task = asyncio.create_task(worker.run())
            await asyncio.wait_for(result_received.wait(), timeout=10.0)

            worker.stop()
            worker_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await worker_task

        assert len(results) == 1
        assert results[0].error is not None
        assert "nonexistent_tool" in results[0].error


# -- 10.3.6: Docker worker test --


_docker_available = shutil.which("docker") is not None


class TestDockerWorker:
    def test_docker_not_found_raises(self, tmp_path: Path, monkeypatch: Any) -> None:
        """DockerWorker should raise DockerNotFoundError when docker is missing."""
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        worker = DockerWorker(
            brain_url="ws://localhost:8080",
            image="orxtra-worker:test",
            root=tmp_path,
            api_key="test-key",
        )
        with pytest.raises(DockerNotFoundError):
            asyncio.run(worker.run())

    @pytest.mark.skipif(
        not _docker_available,
        reason="Docker not available",
    )
    @pytest.mark.asyncio
    async def test_docker_container_start_stop(self, tmp_path: Path) -> None:
        """Verify DockerWorker can start and stop a container.

        Uses the alpine image with a sleep command to test lifecycle.
        This test is skipped if Docker is not available.
        """
        worker = DockerWorker(
            brain_url="ws://localhost:9999",
            image="alpine:latest",
            root=tmp_path,
            api_key="test-key",
        )
        # Start in background.
        run_task = asyncio.create_task(worker.run())

        # Give it a moment to start.
        await asyncio.sleep(2.0)

        # Stop it.
        await worker.stop()

        # Clean up.
        run_task.cancel()
        with contextlib.suppress(asyncio.CancelledError, Exception):
            await run_task
