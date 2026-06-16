from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxt.protocols._tool import ToolError
from orxt.tool._shell_tool import make_shell_tool


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
    proc.terminate = MagicMock()
    return proc


# Default parameters for make_shell_tool to reduce repetition.
_DEFAULTS: dict[str, Any] = {
    "allowed_binaries": ["uv", "pytest", "grep"],
    "description": "test shell tool",
    "read_root": Path("/fake/root"),
    "timeout_ceiling": 30,
    "preview_threshold": 50000,
    "preview_lines": 50,
}


def _make(**overrides: Any) -> Any:  # noqa: ANN401
    """Create a tool with defaults, overriding specific params."""
    kw = {**_DEFAULTS, **overrides}
    return make_shell_tool(**kw)


class TestValidCommand:
    """Tests for successful command execution."""

    @pytest.mark.asyncio
    async def test_valid_command_with_allowed_binary(self) -> None:
        """command='uv sync' with allowed_binaries=['uv'] executes ok."""
        proc = _mock_process(stdout="Resolved 42 packages")
        tool = _make(allowed_binaries=["uv"])
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await tool.execute({"command": "uv sync"})
        result = json.loads(raw)
        assert result["stdout"] == "Resolved 42 packages"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False


class TestBinaryWhitelist:
    """Tests for binary whitelist enforcement."""

    @pytest.mark.asyncio
    async def test_binary_not_in_whitelist(self) -> None:
        """command='rm -rf /' with allowed_binaries=['uv'] raises ToolError."""
        tool = _make(allowed_binaries=["uv"])
        with pytest.raises(ToolError, match="not in the allowed set"):
            await tool.execute({"command": "rm -rf /"})

    @pytest.mark.asyncio
    async def test_empty_command(self) -> None:
        """command='' raises ToolError matching 'Empty command'."""
        tool = _make()
        with pytest.raises(ToolError, match="Empty command"):
            await tool.execute({"command": ""})


class TestCommandParsing:
    """Tests for command tokenization."""

    @pytest.mark.asyncio
    async def test_command_with_arguments(self) -> None:
        """command='pytest -v tests/' passes correct args."""
        proc = _mock_process()
        tool = _make(allowed_binaries=["pytest"])
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"command": "pytest -v tests/"})
        call_args = mock_asyncio.create_subprocess_exec.call_args[0]
        assert call_args == ("pytest", "-v", "tests/")

    @pytest.mark.asyncio
    async def test_quoted_arguments_preserved(self) -> None:
        """command='grep "hello world" file.txt' preserves quoted args."""
        proc = _mock_process()
        tool = _make(allowed_binaries=["grep"])
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"command": 'grep "hello world" file.txt'})
        call_args = mock_asyncio.create_subprocess_exec.call_args[0]
        assert call_args == ("grep", "hello world", "file.txt")


class TestTimeout:
    """Tests for timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_respected(self) -> None:
        """wait_for uses min(requested, ceiling) timeout."""
        proc = _mock_process(stdout="ok")
        tool = _make(timeout_ceiling=30, allowed_binaries=["uv"])

        captured_timeout = None
        real_wait_for = asyncio.wait_for

        async def tracking_wait_for(
            coro: object, *, timeout: float | None = None,  # noqa: ASYNC109
        ) -> object:
            nonlocal captured_timeout
            captured_timeout = timeout
            return await real_wait_for(coro, timeout=timeout)  # type: ignore[arg-type]

        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = tracking_wait_for
            await tool.execute({"command": "uv sync", "timeout": 5})
        assert captured_timeout == 5

    @pytest.mark.asyncio
    async def test_timeout_exceeded_sigterm_then_sigkill(self) -> None:
        """First wait_for times out, second also times out -> SIGKILL."""
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

        tool = _make(timeout_ceiling=10, allowed_binaries=["uv"])
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = fake_wait_for
            raw = await tool.execute({"command": "uv sync"})
        result = json.loads(raw)
        assert result["timed_out"] is True
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


class TestPreview:
    """Tests for output preview behavior."""

    @pytest.mark.asyncio
    async def test_preview_on_large_output(self) -> None:
        """Large stdout exceeds preview_threshold, output contains 'omitted'."""
        large_output = "\n".join(f"line {i}" for i in range(500))
        proc = _mock_process(stdout=large_output)
        tool = _make(
            preview_threshold=100,
            preview_lines=3,
            allowed_binaries=["uv"],
        )
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await tool.execute({"command": "uv sync"})
        result = json.loads(raw)
        assert len(result["stdout"]) < len(large_output)
        assert "omitted" in result["stdout"]


class TestExitCode:
    """Tests for exit code handling."""

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_returned_not_exception(self) -> None:
        """returncode=1 is returned as data in JSON, not raised."""
        proc = _mock_process(returncode=1)
        tool = _make(allowed_binaries=["uv"])
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await tool.execute({"command": "uv sync"})
        result = json.loads(raw)
        assert result["exit_code"] == 1


class TestEnvironment:
    """Tests for environment variable handling."""

    @pytest.mark.asyncio
    async def test_env_override_applied(self) -> None:
        """env_filter={'PATH': '/usr/bin'} is passed to subprocess."""
        proc = _mock_process()
        env = {"PATH": "/usr/bin"}
        tool = _make(allowed_binaries=["uv"], env_filter=env)
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"command": "uv sync"})
        call_kwargs = mock_asyncio.create_subprocess_exec.call_args[1]
        assert call_kwargs["env"] == {"PATH": "/usr/bin"}

    @pytest.mark.asyncio
    async def test_no_env_filter_inherits_parent_env(self) -> None:
        """env_filter=None means no 'env' kwarg is passed."""
        proc = _mock_process()
        tool = _make(allowed_binaries=["uv"], env_filter=None)
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"command": "uv sync"})
        call_kwargs = mock_asyncio.create_subprocess_exec.call_args[1]
        assert "env" not in call_kwargs


class TestWorkingDirectory:
    """Tests for working directory."""

    @pytest.mark.asyncio
    async def test_working_directory_is_read_root(self) -> None:
        """cwd kwarg matches read_root."""
        proc = _mock_process()
        root = Path("/my/project")
        tool = _make(read_root=root, allowed_binaries=["uv"])
        with patch("orxt.tool._shell_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"command": "uv sync"})
        call_kwargs = mock_asyncio.create_subprocess_exec.call_args[1]
        assert call_kwargs["cwd"] == root


class TestPipeline:
    """Tests for pipeline integration."""

    def test_shell_in_file_mutation_tools(self) -> None:
        """'shell' is in FILE_MUTATION_TOOLS."""
        from orxt.tool._pipeline import FILE_MUTATION_TOOLS  # noqa: PLC0415

        assert "shell" in FILE_MUTATION_TOOLS


class TestToolMeta:
    """Tests for tool metadata."""

    def test_tool_name_is_shell(self) -> None:
        """Tool name is 'shell'."""
        tool = _make()
        assert tool.name == "shell"
