"""Tests for the subprocess execution machinery in _subprocess.py.

Covers: arg validation (metachar rejection, path containment),
subprocess execution, timeout enforcement, preview behavior.
The old make_exec_tool/make_shell_tool constructors are deleted (3.4);
these tests cover the surviving run_subprocess and validate_exec_arg.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.protocols import ToolError
from orxtra.tool._subprocess import run_subprocess, validate_exec_arg


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


_DEFAULTS: dict[str, Any] = {
    "executable": "test_bin",
    "args": [],
    "cwd": Path("/fake/root"),
    "timeout": 30,
    "arg_validation": True,
    "preview_threshold": 50000,
    "preview_lines": 50,
}


async def _run(**overrides: Any) -> str:
    """Run run_subprocess with defaults, overriding specific params."""
    kw = {**_DEFAULTS, **overrides}
    result = await run_subprocess(**kw)
    return result.text


class TestSuccessfulExecution:
    """Tests for normal (non-timeout) execution paths."""

    @pytest.mark.asyncio
    async def test_successful_execution_returns_stdout_and_exit_code_zero(
        self,
    ) -> None:
        proc = _mock_process(stdout="hello world")
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await _run()
        result = json.loads(raw)
        assert result["stdout"] == "hello world"
        assert result["exit_code"] == 0
        assert result["timed_out"] is False

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_is_data_not_exception(self) -> None:
        proc = _mock_process(returncode=1)
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await _run()
        result = json.loads(raw)
        assert result["exit_code"] == 1

    @pytest.mark.asyncio
    async def test_args_passed_to_subprocess(self) -> None:
        proc = _mock_process()
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await _run(executable="grep", args=["-r", "pattern", "."])
        call_args = mock_asyncio.create_subprocess_exec.call_args
        assert call_args[0] == ("grep", "-r", "pattern", ".")

    @pytest.mark.asyncio
    async def test_working_directory_is_cwd(self) -> None:
        proc = _mock_process()
        root = Path("/my/project")
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            await _run(cwd=root)
        call_kwargs = mock_asyncio.create_subprocess_exec.call_args[1]
        assert call_kwargs["cwd"] == root

    @pytest.mark.asyncio
    async def test_stderr_captured_in_result(self) -> None:
        proc = _mock_process(stderr="warning: something")
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await _run()
        result = json.loads(raw)
        assert result["stderr"] == "warning: something"

    @pytest.mark.asyncio
    async def test_duration_ms_is_non_negative(self) -> None:
        proc = _mock_process()
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await _run()
        result = json.loads(raw)
        assert result["duration_ms"] >= 0


class TestTimeout:
    """Tests for timeout behavior."""

    @pytest.mark.asyncio
    async def test_timeout_sets_timed_out_true(self) -> None:
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

        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = fake_wait_for
            raw = await _run(timeout=10)
        result = json.loads(raw)
        assert result["timed_out"] is True
        proc.terminate.assert_called_once()

    @pytest.mark.asyncio
    async def test_sigkill_after_grace_period(self) -> None:
        proc = _mock_process()
        proc.terminate = MagicMock()
        proc.kill = MagicMock()
        proc.wait = AsyncMock()

        async def fake_wait_for(
            coro: object, **_kwargs: object,
        ) -> object:
            if hasattr(coro, "close"):
                coro.close()
            raise TimeoutError

        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = fake_wait_for
            raw = await _run(timeout=10)
        result = json.loads(raw)
        assert result["timed_out"] is True
        proc.terminate.assert_called_once()
        proc.kill.assert_called_once()


class TestPreview:
    """Tests for output preview behavior."""

    @pytest.mark.asyncio
    async def test_large_stdout_is_previewed(self) -> None:
        large_output = "\n".join(f"line {i}" for i in range(500))
        proc = _mock_process(stdout=large_output)
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await _run(preview_threshold=100, preview_lines=3)
        result = json.loads(raw)
        assert len(result["stdout"]) < len(large_output)
        assert "omitted" in result["stdout"]


class TestArgValidation:
    """Tests for argument safety (path containment, metachar rejection)."""

    def test_path_within_root_passes(self, tmp_path: Path) -> None:
        subdir = tmp_path / "src"
        subdir.mkdir()
        # Should not raise.
        validate_exec_arg("src/file.py", tmp_path)

    def test_path_escaping_root_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError, match="escapes read root"):
            validate_exec_arg("/etc/passwd", tmp_path)

    def test_traversal_attack_rejected(self, tmp_path: Path) -> None:
        with pytest.raises(ToolError, match="forbidden characters"):
            validate_exec_arg("../../../etc/passwd", tmp_path)

    def test_shell_metachar_dollar_rejected(self) -> None:
        with pytest.raises(ToolError, match="forbidden characters"):
            validate_exec_arg("$HOME", Path("/fake/root"))

    def test_shell_metachar_backtick_rejected(self) -> None:
        with pytest.raises(ToolError, match="forbidden characters"):
            validate_exec_arg("`whoami`", Path("/fake/root"))

    def test_shell_metachar_tilde_rejected(self) -> None:
        with pytest.raises(ToolError, match="forbidden characters"):
            validate_exec_arg("~/secret", Path("/fake/root"))

    @pytest.mark.asyncio
    async def test_arg_validation_false_skips_checks(self) -> None:
        proc = _mock_process(stdout="ok")
        with patch("orxtra.tool._subprocess.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.wait_for = asyncio.wait_for
            raw = await _run(
                arg_validation=False,
                args=["$HOME", "`whoami`"],
            )
        result = json.loads(raw)
        assert result["exit_code"] == 0
