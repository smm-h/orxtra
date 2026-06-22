"""Tests for file write tool constructors."""

from __future__ import annotations

import asyncio
import stat
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from orxtra.protocols._tool import ToolError
from orxtra.tool._write_integration import safe_read_for_write
from orxtra.tool._write_tools import (
    make_copy_tool,
    make_delete_tool,
    make_edit_tool,
    make_mkdir_tool,
    make_move_tool,
    make_set_executable_tool,
    make_write_tool,
)
from orxtra.write_safety import StaleWriteError, StaleWriteTracker, WriteQueue

if TYPE_CHECKING:
    from pathlib import Path


@pytest.fixture
def queue() -> WriteQueue:
    return WriteQueue()


@pytest.fixture
def tracker() -> StaleWriteTracker:
    return StaleWriteTracker()


# ---------------------------------------------------------------------------
# TestWriteTool
# ---------------------------------------------------------------------------


class TestWriteTool:
    """Tests for make_write_tool."""

    @pytest.mark.asyncio
    async def test_write_new_file(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Writing a new file creates it with the correct content."""
        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        target = tmp_path / "new.txt"
        result = (await tool.execute({"path": str(target), "content": "hello"})).text
        assert target.read_text() == "hello"
        assert str(target) in result

    @pytest.mark.asyncio
    async def test_overwrite_after_read(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Overwriting an existing file succeeds after a prior read."""
        target = tmp_path / "existing.txt"
        target.write_text("original")
        await safe_read_for_write(target, queue, tracker, "s1")

        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        await tool.execute({"path": str(target), "content": "updated"})
        assert target.read_text() == "updated"

    @pytest.mark.asyncio
    async def test_overwrite_without_read_raises_stale(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Overwriting an existing file without a prior read raises StaleWriteError."""
        target = tmp_path / "existing.txt"
        target.write_text("original")

        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(StaleWriteError):
            await tool.execute({"path": str(target), "content": "new"})

    @pytest.mark.asyncio
    async def test_create_dirs(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """create_dirs=True creates parent directories."""
        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        target = tmp_path / "a" / "b" / "file.txt"
        await tool.execute(
            {"path": str(target), "content": "deep", "create_dirs": True},
        )
        assert target.read_text() == "deep"

    @pytest.mark.asyncio
    async def test_missing_parent_raises(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Writing without create_dirs and missing parent raises FileNotFoundError."""
        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        target = tmp_path / "no_such_dir" / "file.txt"
        with pytest.raises(FileNotFoundError):
            await tool.execute({"path": str(target), "content": "x"})

    @pytest.mark.asyncio
    async def test_path_escape_raises_tool_error(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """A path escaping the read root raises ToolError, not PathError."""
        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="escapes root boundary"):
            await tool.execute({"path": "/etc/passwd", "content": "x"})

    @pytest.mark.asyncio
    async def test_write_scope_violation(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Writing outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        tool = make_write_tool(tmp_path, [allowed], queue, tracker, "s1")
        target = tmp_path / "forbidden.txt"
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({"path": str(target), "content": "x"})

    @pytest.mark.asyncio
    async def test_concurrent_writes(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Concurrent writes to same new file both complete (serialized)."""
        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        target = tmp_path / "race.txt"
        results: list[str] = []

        async def writer(content: str) -> None:
            r = await tool.execute({"path": str(target), "content": content})
            results.append(r)

        await asyncio.gather(writer("a"), writer("b"))
        assert len(results) == 2
        # The file has one of the two values (last writer wins)
        assert target.read_text() in ("a", "b")

    @pytest.mark.asyncio
    async def test_invalid_args_raises_tool_error(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Missing required arguments raises ToolError."""
        tool = make_write_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute({"path": str(tmp_path / "f.txt")})


# ---------------------------------------------------------------------------
# TestEditTool
# ---------------------------------------------------------------------------


class TestEditTool:
    """Tests for make_edit_tool."""

    @pytest.mark.asyncio
    async def test_single_replacement(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Single match replacement updates content correctly."""
        target = tmp_path / "file.txt"
        target.write_text("hello world")

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = (await tool.execute({
            "path": str(target),
            "old_string": "world",
            "new_string": "earth",
        })).text
        assert target.read_text() == "hello earth"
        assert result == f"Edited {target}"

    @pytest.mark.asyncio
    async def test_no_match_raises(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Searching for a string not in the file raises ToolError."""
        target = tmp_path / "file.txt"
        target.write_text("hello world")

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="not found"):
            await tool.execute({
                "path": str(target),
                "old_string": "xyz",
                "new_string": "abc",
            })

    @pytest.mark.asyncio
    async def test_multiple_matches_without_replace_all_raises(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Multiple matches without replace_all raises ToolError."""
        target = tmp_path / "file.txt"
        target.write_text("aaa bbb aaa")

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="Multiple matches"):
            await tool.execute({
                "path": str(target),
                "old_string": "aaa",
                "new_string": "ccc",
            })

    @pytest.mark.asyncio
    async def test_replace_all(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """replace_all=True replaces all occurrences."""
        target = tmp_path / "file.txt"
        target.write_text("aaa bbb aaa")

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        result = (await tool.execute({
            "path": str(target),
            "old_string": "aaa",
            "new_string": "ccc",
            "replace_all": True,
        })).text
        assert target.read_text() == "ccc bbb ccc"
        assert "2 replacements" in result

    @pytest.mark.asyncio
    async def test_empty_old_string_raises(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Empty old_string raises ToolError."""
        target = tmp_path / "file.txt"
        target.write_text("hello")

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="must not be empty"):
            await tool.execute({
                "path": str(target),
                "old_string": "",
                "new_string": "x",
            })

    @pytest.mark.asyncio
    async def test_edit_nonexistent_file(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Editing a non-existent file raises FileNotFoundError."""
        target = tmp_path / "missing.txt"

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(FileNotFoundError):
            await tool.execute({
                "path": str(target),
                "old_string": "x",
                "new_string": "y",
            })

    @pytest.mark.asyncio
    async def test_edit_preserves_unmodified_content(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Parts of the file not matching old_string are preserved."""
        target = tmp_path / "file.txt"
        target.write_text("line1\nline2\nline3\n")

        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        await tool.execute({
            "path": str(target),
            "old_string": "line2",
            "new_string": "REPLACED",
        })
        assert target.read_text() == "line1\nREPLACED\nline3\n"

    @pytest.mark.asyncio
    async def test_edit_path_escape_raises_tool_error(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Path escape raises ToolError."""
        tool = make_edit_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="escapes root boundary"):
            await tool.execute({
                "path": "/etc/passwd",
                "old_string": "x",
                "new_string": "y",
            })

    @pytest.mark.asyncio
    async def test_edit_write_scope_violation(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Edit outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "forbidden.txt"
        target.write_text("hi")

        tool = make_edit_tool(tmp_path, [allowed], queue, tracker, "s1")
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({
                "path": str(target),
                "old_string": "hi",
                "new_string": "bye",
            })


# ---------------------------------------------------------------------------
# TestMkdirTool
# ---------------------------------------------------------------------------


class TestMkdirTool:
    """Tests for make_mkdir_tool."""

    @pytest.mark.asyncio
    async def test_create_new_directory(self, tmp_path: Path) -> None:
        """Creates a new directory."""
        tool = make_mkdir_tool(tmp_path, None)
        target = tmp_path / "newdir"
        result = (await tool.execute({"path": str(target)})).text
        assert target.is_dir()
        assert str(target) in result

    @pytest.mark.asyncio
    async def test_create_nested_directories(self, tmp_path: Path) -> None:
        """Creates nested parent directories."""
        tool = make_mkdir_tool(tmp_path, None)
        target = tmp_path / "a" / "b" / "c"
        await tool.execute({"path": str(target)})
        assert target.is_dir()

    @pytest.mark.asyncio
    async def test_already_exists_no_error(self, tmp_path: Path) -> None:
        """Calling mkdir on an existing directory does not error."""
        tool = make_mkdir_tool(tmp_path, None)
        target = tmp_path / "existing"
        target.mkdir()
        result = (await tool.execute({"path": str(target)})).text
        assert target.is_dir()
        assert str(target) in result

    @pytest.mark.asyncio
    async def test_mkdir_write_scope_violation(self, tmp_path: Path) -> None:
        """mkdir outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        tool = make_mkdir_tool(tmp_path, [allowed])
        target = tmp_path / "forbidden"
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({"path": str(target)})


# ---------------------------------------------------------------------------
# TestMoveTool
# ---------------------------------------------------------------------------


class TestMoveTool:
    """Tests for make_move_tool."""

    @pytest.mark.asyncio
    async def test_move_file(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Moving a file removes the source and creates the destination."""
        src = tmp_path / "src.txt"
        src.write_text("content")
        dst = tmp_path / "dst.txt"

        tool = make_move_tool(tmp_path, None, queue, tracker, "s1")
        result = (await tool.execute({
            "source": str(src),
            "destination": str(dst),
        })).text
        assert not src.exists()
        assert dst.read_text() == "content"
        assert str(src) in result
        assert str(dst) in result

    @pytest.mark.asyncio
    async def test_move_destination_outside_scope(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Moving to a destination outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        src = allowed / "src.txt"
        src.write_text("content")
        dst = tmp_path / "forbidden.txt"

        tool = make_move_tool(tmp_path, [allowed], queue, tracker, "s1")
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({
                "source": str(src),
                "destination": str(dst),
            })

    @pytest.mark.asyncio
    async def test_move_source_outside_scope(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Moving from a source outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        src = tmp_path / "outside.txt"
        src.write_text("content")
        dst = allowed / "dst.txt"

        tool = make_move_tool(tmp_path, [allowed], queue, tracker, "s1")
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({
                "source": str(src),
                "destination": str(dst),
            })

    @pytest.mark.asyncio
    async def test_move_directory(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Moving a directory works."""
        src_dir = tmp_path / "srcdir"
        src_dir.mkdir()
        (src_dir / "file.txt").write_text("inside")
        dst_dir = tmp_path / "dstdir"

        tool = make_move_tool(tmp_path, None, queue, tracker, "s1")
        await tool.execute({
            "source": str(src_dir),
            "destination": str(dst_dir),
        })
        assert not src_dir.exists()
        assert (dst_dir / "file.txt").read_text() == "inside"

    @pytest.mark.asyncio
    async def test_move_nonexistent_source(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Moving a non-existent source raises ToolError."""
        tool = make_move_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="Source does not exist"):
            await tool.execute({
                "source": str(tmp_path / "missing"),
                "destination": str(tmp_path / "dst"),
            })


# ---------------------------------------------------------------------------
# TestCopyTool
# ---------------------------------------------------------------------------


class TestCopyTool:
    """Tests for make_copy_tool."""

    @pytest.mark.asyncio
    async def test_copy_file(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Copying a file leaves source unchanged and creates destination."""
        src = tmp_path / "src.txt"
        src.write_text("content")
        dst = tmp_path / "dst.txt"

        tool = make_copy_tool(tmp_path, None, queue, tracker, "s1")
        result = (await tool.execute({
            "source": str(src),
            "destination": str(dst),
        })).text
        assert src.read_text() == "content"
        assert dst.read_text() == "content"
        assert str(src) in result
        assert str(dst) in result

    @pytest.mark.asyncio
    async def test_copy_source_outside_write_scope_allowed(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Source only needs read root containment, not write scope."""
        write_dir = tmp_path / "writable"
        write_dir.mkdir()
        # Source is in read root but outside write scope
        src = tmp_path / "readonly.txt"
        src.write_text("data")
        dst = write_dir / "copy.txt"

        tool = make_copy_tool(tmp_path, [write_dir], queue, tracker, "s1")
        await tool.execute({
            "source": str(src),
            "destination": str(dst),
        })
        assert dst.read_text() == "data"

    @pytest.mark.asyncio
    async def test_copy_destination_outside_scope(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Copying to a destination outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        src = allowed / "src.txt"
        src.write_text("content")
        dst = tmp_path / "forbidden.txt"

        tool = make_copy_tool(tmp_path, [allowed], queue, tracker, "s1")
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({
                "source": str(src),
                "destination": str(dst),
            })

    @pytest.mark.asyncio
    async def test_copy_nonexistent_source(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Copying a non-existent source raises ToolError."""
        tool = make_copy_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="Source does not exist"):
            await tool.execute({
                "source": str(tmp_path / "missing"),
                "destination": str(tmp_path / "dst"),
            })

    @pytest.mark.asyncio
    async def test_copy_directory_source_raises(
        self,
        tmp_path: Path,
        queue: WriteQueue,
        tracker: StaleWriteTracker,
    ) -> None:
        """Copying a directory (not a file) raises ToolError."""
        src_dir = tmp_path / "srcdir"
        src_dir.mkdir()
        dst = tmp_path / "dst"

        tool = make_copy_tool(tmp_path, None, queue, tracker, "s1")
        with pytest.raises(ToolError, match="not a file"):
            await tool.execute({
                "source": str(src_dir),
                "destination": str(dst),
            })


# ---------------------------------------------------------------------------
# TestDeleteTool
# ---------------------------------------------------------------------------


class TestDeleteTool:
    """Tests for make_delete_tool (mocked saferm subprocess)."""

    @pytest.mark.asyncio
    async def test_delete_file(self, tmp_path: Path) -> None:
        """Delete file calls saferm with correct arguments."""
        target = tmp_path / "file.txt"
        target.write_text("doomed")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        tool = make_delete_tool(tmp_path, None)
        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc,
        ) as mock_exec:
            result = (await tool.execute({
                "path": str(target),
                "description": "test deletion",
                "recursive": False,
            })).text

        mock_exec.assert_called_once()
        call_args = mock_exec.call_args[0]
        assert call_args[0] == "saferm"
        assert call_args[1] == "delete"
        assert "--description" in call_args
        assert "test deletion" in call_args
        assert "-r" not in call_args
        assert str(target) == call_args[-1]
        assert str(target) in result

    @pytest.mark.asyncio
    async def test_delete_directory_without_recursive_raises(
        self, tmp_path: Path,
    ) -> None:
        """Deleting a directory without recursive=True raises ToolError."""
        target = tmp_path / "somedir"
        target.mkdir()

        tool = make_delete_tool(tmp_path, None)
        with pytest.raises(ToolError, match="set recursive=true"):
            await tool.execute({
                "path": str(target),
                "description": "test",
                "recursive": False,
            })

    @pytest.mark.asyncio
    async def test_delete_directory_recursive(self, tmp_path: Path) -> None:
        """Deleting a directory with recursive=True passes -r to saferm."""
        target = tmp_path / "somedir"
        target.mkdir()

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        tool = make_delete_tool(tmp_path, None)
        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc,
        ) as mock_exec:
            await tool.execute({
                "path": str(target),
                "description": "remove dir",
                "recursive": True,
            })

        call_args = mock_exec.call_args[0]
        assert "-r" in call_args

    @pytest.mark.asyncio
    async def test_delete_description_passed(self, tmp_path: Path) -> None:
        """The description argument is forwarded to saferm."""
        target = tmp_path / "file.txt"
        target.write_text("x")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"")
        mock_proc.returncode = 0

        tool = make_delete_tool(tmp_path, None)
        with patch(
            "asyncio.create_subprocess_exec", return_value=mock_proc,
        ) as mock_exec:
            await tool.execute({
                "path": str(target),
                "description": "specific reason",
                "recursive": False,
            })

        call_args = mock_exec.call_args[0]
        desc_idx = list(call_args).index("--description")
        assert call_args[desc_idx + 1] == "specific reason"

    @pytest.mark.asyncio
    async def test_delete_saferm_failure(self, tmp_path: Path) -> None:
        """saferm returning non-zero raises ToolError with stderr."""
        target = tmp_path / "file.txt"
        target.write_text("x")

        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (b"", b"something went wrong")
        mock_proc.returncode = 1

        tool = make_delete_tool(tmp_path, None)
        with (
            patch("asyncio.create_subprocess_exec", return_value=mock_proc),
            pytest.raises(ToolError, match=r"saferm failed.*something went wrong"),
        ):
                await tool.execute({
                    "path": str(target),
                    "description": "test",
                    "recursive": False,
                })

    @pytest.mark.asyncio
    async def test_delete_write_scope_violation(self, tmp_path: Path) -> None:
        """Deleting outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "forbidden.txt"
        target.write_text("x")

        tool = make_delete_tool(tmp_path, [allowed])
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({
                "path": str(target),
                "description": "test",
                "recursive": False,
            })


# ---------------------------------------------------------------------------
# TestSetExecutableTool
# ---------------------------------------------------------------------------


class TestSetExecutableTool:
    """Tests for make_set_executable_tool."""

    @pytest.mark.asyncio
    async def test_set_executable(self, tmp_path: Path) -> None:
        """Sets executable bits on a file."""
        target = tmp_path / "script.sh"
        target.write_text("#!/bin/bash\n")
        # Remove any executable bits first
        target.chmod(0o644)

        tool = make_set_executable_tool(tmp_path, None)
        result = (await tool.execute({"path": str(target)})).text
        mode = target.stat().st_mode
        assert mode & stat.S_IXUSR
        assert mode & stat.S_IXGRP
        assert mode & stat.S_IXOTH
        assert str(target) in result

    @pytest.mark.asyncio
    async def test_already_executable(self, tmp_path: Path) -> None:
        """Calling on an already-executable file is a no-op (no error)."""
        target = tmp_path / "script.sh"
        target.write_text("#!/bin/bash\n")
        target.chmod(0o755)

        tool = make_set_executable_tool(tmp_path, None)
        result = (await tool.execute({"path": str(target)})).text
        mode = target.stat().st_mode
        assert mode & stat.S_IXUSR
        assert str(target) in result

    @pytest.mark.asyncio
    async def test_set_executable_write_scope_violation(
        self, tmp_path: Path,
    ) -> None:
        """set_executable outside write scope raises ToolError."""
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        target = tmp_path / "forbidden.sh"
        target.write_text("#!/bin/bash\n")

        tool = make_set_executable_tool(tmp_path, [allowed])
        with pytest.raises(ToolError, match="outside write scope"):
            await tool.execute({"path": str(target)})

    @pytest.mark.asyncio
    async def test_set_executable_nonexistent(self, tmp_path: Path) -> None:
        """set_executable on a non-existent file raises ToolError."""
        tool = make_set_executable_tool(tmp_path, None)
        with pytest.raises(ToolError, match="File not found"):
            await tool.execute({"path": str(tmp_path / "missing.sh")})

    @pytest.mark.asyncio
    async def test_set_executable_on_directory(self, tmp_path: Path) -> None:
        """set_executable on a directory raises ToolError."""
        target = tmp_path / "somedir"
        target.mkdir()

        tool = make_set_executable_tool(tmp_path, None)
        with pytest.raises(ToolError, match="Not a file"):
            await tool.execute({"path": str(target)})
