from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from orxtra.protocols._tool import ToolError
from orxtra.tool._git_tool import make_git_tool


def _mock_process(
    stdout: str = "", stderr: str = "", returncode: int = 0
) -> MagicMock:
    """Create a mock subprocess with canned output."""
    proc = MagicMock()
    proc.communicate = AsyncMock(
        return_value=(stdout.encode(), stderr.encode())
    )
    proc.returncode = returncode
    proc.kill = MagicMock()
    proc.wait = AsyncMock()
    return proc


_ROOT = Path("/fake/repo")
_ALL_SUBCOMMANDS = [
    "status",
    "diff",
    "log",
    "show",
    "blame",
    "branches",
    "changed_files",
    "commit",
]


class TestMakeGitTool:
    """Tests for the make_git_tool constructor."""

    def test_returns_tool_with_correct_name(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        assert tool.name == "git"

    def test_returns_tool_with_parameters_schema(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        assert tool.parameters["type"] == "object"
        assert "subcommand" in tool.parameters["properties"]

    def test_unknown_subcommand_in_allowed_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="Unknown git subcommands"):
            make_git_tool(_ROOT, ["status", "rebase"])


class TestSubcommandValidation:
    """Tests for subcommand allow-list enforcement."""

    @pytest.mark.asyncio
    async def test_allowed_subcommand_proceeds(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        proc = _mock_process(stdout="M file.py")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "status"})
        assert result == "M file.py"

    @pytest.mark.asyncio
    async def test_disallowed_subcommand_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        with pytest.raises(ToolError, match="not allowed"):
            await tool.execute({"subcommand": "diff"})

    @pytest.mark.asyncio
    async def test_nonexistent_subcommand_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        with pytest.raises(ToolError, match="not allowed"):
            await tool.execute({"subcommand": "rebase"})


class TestStatusSubcommand:
    """Tests for the status subcommand."""

    @pytest.mark.asyncio
    async def test_runs_git_status_porcelain(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        proc = _mock_process(stdout="M src/main.py\n?? new.txt")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "status"})
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "status",
            "--porcelain",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "M src/main.py\n?? new.txt"

    @pytest.mark.asyncio
    async def test_empty_status_returns_no_output(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        proc = _mock_process(stdout="")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "status"})
        assert result == "(no output)"


class TestDiffSubcommand:
    """Tests for the diff subcommand."""

    @pytest.mark.asyncio
    async def test_runs_git_diff(self) -> None:
        tool = make_git_tool(_ROOT, ["diff"])
        proc = _mock_process(stdout="diff --git a/f.py b/f.py")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "diff"})
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "diff",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "diff --git a/f.py b/f.py"

    @pytest.mark.asyncio
    async def test_diff_with_args(self) -> None:
        tool = make_git_tool(_ROOT, ["diff"])
        proc = _mock_process(stdout="changed content")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {"subcommand": "diff", "args": ["--cached", "file.py"]}
            )
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "diff",
            "--cached",
            "file.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "changed content"


class TestLogSubcommand:
    """Tests for the log subcommand."""

    @pytest.mark.asyncio
    async def test_default_log_uses_oneline_20(self) -> None:
        tool = make_git_tool(_ROOT, ["log"])
        proc = _mock_process(stdout="abc1234 Initial commit")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "log"})
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "log",
            "--oneline",
            "-20",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "abc1234 Initial commit"

    @pytest.mark.asyncio
    async def test_log_with_custom_args_overrides_defaults(self) -> None:
        tool = make_git_tool(_ROOT, ["log"])
        proc = _mock_process(stdout="full log output")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {"subcommand": "log", "args": ["--graph", "-5"]}
            )
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "log",
            "--graph",
            "-5",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "full log output"


class TestShowSubcommand:
    """Tests for the show subcommand."""

    @pytest.mark.asyncio
    async def test_runs_git_show_with_args(self) -> None:
        tool = make_git_tool(_ROOT, ["show"])
        proc = _mock_process(stdout="commit abc\nAuthor: test")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {"subcommand": "show", "args": ["HEAD"]}
            )
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "show",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "commit abc\nAuthor: test"


class TestBlameSubcommand:
    """Tests for the blame subcommand."""

    @pytest.mark.asyncio
    async def test_runs_git_blame_with_args(self) -> None:
        tool = make_git_tool(_ROOT, ["blame"])
        proc = _mock_process(stdout="abc1234 (author 2024-01-01) line")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {"subcommand": "blame", "args": ["src/main.py"]}
            )
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "blame",
            "src/main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "abc1234 (author 2024-01-01) line"


class TestBranchesSubcommand:
    """Tests for the branches subcommand."""

    @pytest.mark.asyncio
    async def test_runs_git_branch_all(self) -> None:
        tool = make_git_tool(_ROOT, ["branches"])
        proc = _mock_process(stdout="* main\n  feature/x")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "branches"})
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "branch",
            "-a",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "* main\n  feature/x"


class TestChangedFilesSubcommand:
    """Tests for the changed_files subcommand."""

    @pytest.mark.asyncio
    async def test_runs_git_diff_name_only_head(self) -> None:
        tool = make_git_tool(_ROOT, ["changed_files"])
        proc = _mock_process(stdout="file1.py\nfile2.py")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "changed_files"})
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "git",
            "diff",
            "--name-only",
            "HEAD",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "file1.py\nfile2.py"


class TestCommitSubcommand:
    """Tests for the commit subcommand (mutation tier, uses safegit)."""

    @pytest.mark.asyncio
    async def test_valid_commit_calls_safegit(self) -> None:
        tool = make_git_tool(_ROOT, ["commit"])
        proc = _mock_process(stdout="[main abc1234] Fix bug")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {
                    "subcommand": "commit",
                    "args": ["Fix bug", "src/main.py"],
                }
            )
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "safegit",
            "commit",
            "-m",
            "Fix bug",
            "--",
            "src/main.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "[main abc1234] Fix bug"

    @pytest.mark.asyncio
    async def test_commit_with_multiple_files(self) -> None:
        tool = make_git_tool(_ROOT, ["commit"])
        proc = _mock_process(stdout="committed")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {
                    "subcommand": "commit",
                    "args": ["Add feature", "a.py", "b.py", "c.py"],
                }
            )
        mock_asyncio.create_subprocess_exec.assert_called_once_with(
            "safegit",
            "commit",
            "-m",
            "Add feature",
            "--",
            "a.py",
            "b.py",
            "c.py",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=_ROOT,
        )
        assert result == "committed"

    @pytest.mark.asyncio
    async def test_commit_with_trailers(self) -> None:
        tool = make_git_tool(
            _ROOT,
            ["commit"],
            run_context={"Session-Id": "abc123", "Task-Id": "t42"},
        )
        proc = _mock_process(stdout="committed with trailers")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {
                    "subcommand": "commit",
                    "args": ["Fix thing", "file.py"],
                }
            )
        call_args = mock_asyncio.create_subprocess_exec.call_args
        cmd = call_args[0]
        assert cmd[0] == "safegit"
        assert "--trailer" in cmd
        # Find trailer positions and check values
        trailer_indices = [i for i, v in enumerate(cmd) if v == "--trailer"]
        trailers = [cmd[i + 1] for i in trailer_indices]
        assert "Session-Id: abc123" in trailers
        assert "Task-Id: t42" in trailers
        assert result == "committed with trailers"

    @pytest.mark.asyncio
    async def test_commit_empty_message_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["commit"])
        with pytest.raises(ToolError, match="Commit message must be non-empty"):
            await tool.execute(
                {"subcommand": "commit", "args": ["", "file.py"]}
            )

    @pytest.mark.asyncio
    async def test_commit_no_args_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["commit"])
        with pytest.raises(ToolError, match="Commit message must be non-empty"):
            await tool.execute({"subcommand": "commit", "args": []})

    @pytest.mark.asyncio
    async def test_commit_message_only_no_files_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["commit"])
        with pytest.raises(
            ToolError, match="Commit requires at least one file"
        ):
            await tool.execute(
                {"subcommand": "commit", "args": ["message only"]}
            )

    @pytest.mark.asyncio
    async def test_commit_nonzero_exit_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["commit"])
        proc = _mock_process(stderr="nothing to commit", returncode=1)
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            with pytest.raises(ToolError, match="nothing to commit"):
                await tool.execute(
                    {
                        "subcommand": "commit",
                        "args": ["msg", "file.py"],
                    }
                )


class TestErrorHandling:
    """Tests for timeout and error scenarios."""

    @pytest.mark.asyncio
    async def test_timeout_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        proc = MagicMock()
        proc.communicate = AsyncMock(
            return_value=(b"", b""),
        )
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = TimeoutError

            async def fake_wait_for(
                coro: object, **_kwargs: object
            ) -> object:
                # Consume the coroutine to avoid warnings
                if hasattr(coro, "close"):
                    coro.close()  # type: ignore[union-attr]
                raise TimeoutError

            mock_asyncio.wait_for = fake_wait_for
            with pytest.raises(ToolError, match="Git command timed out"):
                await tool.execute({"subcommand": "status"})

    @pytest.mark.asyncio
    async def test_readonly_nonzero_exit_returns_stderr(self) -> None:
        tool = make_git_tool(_ROOT, ["diff"])
        proc = _mock_process(stderr="fatal: bad revision", returncode=128)
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute(
                {"subcommand": "diff", "args": ["nonexistent"]}
            )
        # Read-only commands return stderr on failure, no exception
        assert result == "fatal: bad revision"

    @pytest.mark.asyncio
    async def test_readonly_nonzero_exit_empty_stderr_returns_exit_code(
        self,
    ) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        proc = _mock_process(stderr="", returncode=1)
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            result = await tool.execute({"subcommand": "status"})
        assert result == "(exit code 1)"

    @pytest.mark.asyncio
    async def test_working_directory_is_read_root(self) -> None:
        custom_root = Path("/custom/project")
        tool = make_git_tool(custom_root, ["status"])
        proc = _mock_process(stdout="clean")
        with patch("orxtra.tool._git_tool.asyncio") as mock_asyncio:
            mock_asyncio.create_subprocess_exec = AsyncMock(return_value=proc)
            mock_asyncio.subprocess = asyncio.subprocess
            mock_asyncio.TimeoutError = asyncio.TimeoutError
            mock_asyncio.wait_for = asyncio.wait_for
            await tool.execute({"subcommand": "status"})
        call_kwargs = mock_asyncio.create_subprocess_exec.call_args[1]
        assert call_kwargs["cwd"] == custom_root

    @pytest.mark.asyncio
    async def test_invalid_args_schema_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute({"subcommand": "status", "args": "not-a-list"})

    @pytest.mark.asyncio
    async def test_missing_subcommand_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute({"args": ["something"]})

    @pytest.mark.asyncio
    async def test_extra_field_raises_tool_error(self) -> None:
        tool = make_git_tool(_ROOT, ["status"])
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute(
                {"subcommand": "status", "unknown_field": True}
            )
