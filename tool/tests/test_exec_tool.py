from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.protocols._tool import ToolError
from orxtra.tool._exec_tool import make_exec_tool


def _mock_process(
    stdout: str = "", stderr: str = "", returncode: int = 0,
) -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode()),
    )
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


# Default parameters for make_exec_tool to reduce repetition.
_DEFAULTS: dict[str, Any] = {
    "executable": "test_bin",
    "description": "test tool",
    "arg_schema": {},
    "read_root": Path("/fake/root"),
    "timeout_ceiling": 30,
    "preview_threshold": 50000,
    "preview_lines": 50,
}


def _make(**overrides: Any) -> Any:  # noqa: ANN401
    """Create a tool with defaults, overriding specific params."""
    kw = {**_DEFAULTS, **overrides}
    return make_exec_tool(**kw)


class TestMakeExecTool:
    """Constructor-level tests."""

    def test_tool_name_is_executable(self) -> None:
        """Tool name equals the executable name."""
        tool = _make(executable="my_binary")
        assert tool.name == "my_binary"

    def test_tool_merges_arg_schema(self) -> None:
        """Additional arg_schema properties appear in tool parameters."""
        extra = {
            "pattern": {
                "type": "string",
                "description": "Search pattern.",
            },
        }
        tool = _make(arg_schema=extra)
        props = tool.parameters["properties"]
        assert "pattern" in props
        assert props["pattern"]["type"] == "string"
        # Base properties still present.
        assert "args" in props
        assert "timeout" in props


class TestSuccessfulExecution:
    """Tests for normal (non-timeout) execution paths."""

    @pytest.mark.asyncio
    async def test_successful_execution_returns_stdout_and_exit_code_zero(
        self,
    ) -> None:
        """Run with stdout, verify JSON result."""
        proc = _mock_process(stdout="hello world")
        tool = _make()
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        assert result["stdout"] == "hello world"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_is_data_not_exception(self) -> None:
        """Non-zero exit code is returned as data, not raised."""
        proc = _mock_process(returncode=1)
        tool = _make()
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_args_passed_to_subprocess(self) -> None:
        """The mock receives the correct arguments."""
        proc = _mock_process()
        tool = _make(executable="grep")
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"args": ["-r", "pattern", "."]})
        call_args = mock_asyncio.create_subprocess_exec.call_args
        assert call_args[0] == ("grep", "-r", "pattern", ".")

    @pytest.mark.asyncio
    async def test_working_directory_is_read_root(self) -> None:
        """cwd kwarg matches read_root."""
        proc = _mock_process()
        root = Path("/my/project")
        tool = _make(read_root=root)
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({})
        call_kwargs = mock_asyncio.create_subprocess_exec.call_args[1]
        assert call_kwargs["cwd"] == root

    @pytest.mark.asyncio
    async def test_empty_args_runs_just_executable(self) -> None:
        """No args passed, executable runs alone."""
        proc = _mock_process()
        tool = _make(executable="ls")
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({})
        call_args = mock_asyncio.create_subprocess_exec.call_args[0]
        assert call_args == ("ls",)

    @pytest.mark.asyncio
    async def test_stderr_captured_in_result(self) -> None:
        """stderr content appears in result."""
        proc = _mock_process(stderr="warning: something")
        tool = _make()
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        assert result["stderr"] == "warning: something"

    @pytest.mark.asyncio
    async def test_duration_ms_is_positive(self) -> None:
        """duration_ms > 0."""
        proc = _mock_process()
        tool = _make()
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        assert result["duration_ms"] >= 0


class TestTimeout:
    """Tests for timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_sets_timed_out_true(self) -> None:
        """When wait_for raises TimeoutError, result has timed_out=True."""
        proc = _mock_process()
        proc.terminate = MagicMock()

        call_count = 0

        async def fake_wait_for(
            coro: object, **_kwargs: object,
        ) -> object:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                if hasattr(coro, "close"):
                    coro.close()
                raise TimeoutError
            return await coro  # type: ignore[misc]

        tool = _make(timeout_ceiling=10)
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = fake_wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        assert result["timed_out"] is True
        proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_sigkill_after_grace_period(self) -> None:
        """Process ignores SIGTERM, so SIGKILL is sent."""
        proc = _mock_process()
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        call_count = 0

        async def fake_wait_for(
            coro: object, **_kwargs: object,
        ) -> object:
            nonlocal call_count
            call_count += 1
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError

        tool = _make(timeout_ceiling=10)
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = fake_wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        assert result["timed_out"] is True
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()

    @pytest.mark.asyncio
    async def test_timeout_ceiling_enforced(self) -> None:
        """Requested timeout of 999 is capped at ceiling of 10."""
        proc = _mock_process(stdout="ok")
        ceiling = 10
        tool = _make(timeout_ceiling=ceiling)

        captured_timeout = None
        real_wait_for = asyncio.wait_for

        async def tracking_wait_for(
            coro: object, *, timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            nonlocal captured_timeout
            captured_timeout = timeout
            return await real_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = tracking_wait_for
            await tool.execute({"timeout": 999})
        assert captured_timeout == ceiling


class TestPreview:
    """Tests for output preview behavior."""

    @pytest.mark.asyncio
    async def test_large_stdout_is_previewed(self) -> None:
        """When stdout exceeds preview_threshold, it's previewed."""
        large_output = "\n".join(f"line {i}" for i in range(500))
        proc = _mock_process(stdout=large_output)
        tool = _make(preview_threshold=100, preview_lines=3)
        with patch("orxtra.tool._exec_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = (await tool.execute({})).text
        result = json.loads(raw)
        # The full output is much larger than the preview.
        assert len(result["stdout"]) < len(large_output)
        assert "omitted" in result["stdout"]


class TestValidation:
    """Tests for argument validation via the exec tool schema."""

    @pytest.mark.asyncio
    async def test_custom_arg_schema_validation(self) -> None:
        """Additional schema is validated -- wrong type raises ToolError."""
        extra = {
            "pattern": {
                "type": "string",
                "description": "Search pattern.",
            },
        }
        tool = _make(arg_schema=extra)
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute({"pattern": 12345})
