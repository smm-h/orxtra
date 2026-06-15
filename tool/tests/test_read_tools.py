"""Tests for file read tool constructors."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
from orxt.protocols._tool import ToolError
from orxt.tool._read_tools import (
    make_diff_tool,
    make_glob_tool,
    make_grep_tool,
    make_list_dir_tool,
    make_read_tool,
    make_stat_tool,
)

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write(path: Path, content: str) -> None:
    """Write text content to a file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _write_binary(path: Path, data: bytes) -> None:
    """Write binary data to a file, creating parents."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


# ---------------------------------------------------------------------------
# TestMakeReadTool
# ---------------------------------------------------------------------------


class TestMakeReadTool:
    """Tests for the read file tool constructor."""

    @pytest.mark.asyncio
    async def test_read_existing_file_with_line_numbers(self, tmp_path: Path) -> None:
        """Reading an existing file returns content with cat -n line numbers."""
        _write(tmp_path / "hello.txt", "alpha\nbeta\ngamma\n")
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "hello.txt"})
        assert "1\talpha" in result
        assert "2\tbeta" in result
        assert "3\tgamma" in result

    @pytest.mark.asyncio
    async def test_read_with_offset_and_limit(self, tmp_path: Path) -> None:
        """Reading with offset and limit returns the correct line range."""
        lines = "\n".join(f"line{i}" for i in range(1, 11))
        _write(tmp_path / "ten.txt", lines)
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "ten.txt", "offset": 3, "limit": 2})
        assert "line3" in result
        assert "line4" in result
        assert "line2" not in result
        assert "line5" not in result

    @pytest.mark.asyncio
    async def test_read_nonexistent_file_raises(self, tmp_path: Path) -> None:
        """Reading a non-existent file raises ToolError."""
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        with pytest.raises(ToolError, match="File not found"):
            await tool.execute({"path": "missing.txt"})

    @pytest.mark.asyncio
    async def test_read_binary_file_returns_rejection(self, tmp_path: Path) -> None:
        """Reading a binary file returns the binary rejection message."""
        _write_binary(tmp_path / "bin.dat", b"\x00\x01\x02\xff")
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "bin.dat"})
        assert result == "Binary file, cannot display"

    @pytest.mark.asyncio
    async def test_read_path_escape_raises(self, tmp_path: Path) -> None:
        """Path escape via ../ raises ToolError (PathError converted)."""
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        with pytest.raises(ToolError, match="escapes root boundary"):
            await tool.execute({"path": "../escape.txt"})

    @pytest.mark.asyncio
    async def test_large_file_returns_preview(self, tmp_path: Path) -> None:
        """A file exceeding preview_threshold returns a preview."""
        content = "\n".join(f"line-{i}" for i in range(1, 51))
        _write(tmp_path / "large.txt", content)
        # Low threshold so even small content triggers preview
        tool = make_read_tool(tmp_path, preview_threshold=10, preview_lines=3)
        result = await tool.execute({"path": "large.txt"})
        assert "omitted" in result
        assert "total lines" in result

    @pytest.mark.asyncio
    async def test_large_file_full_after_preview(self, tmp_path: Path) -> None:
        """After a preview, full=true returns full content with line numbers."""
        content = "\n".join(f"line-{i}" for i in range(1, 21))
        _write(tmp_path / "big.txt", content)
        tool = make_read_tool(tmp_path, preview_threshold=10, preview_lines=3)
        # First call triggers preview
        first = await tool.execute({"path": "big.txt"})
        assert "omitted" in first
        # Second call with full=true returns all content
        second = await tool.execute({"path": "big.txt", "full": True})
        assert "omitted" not in second
        assert "line-1" in second
        assert "line-20" in second
        # Should have line numbers
        assert "\t" in second

    @pytest.mark.asyncio
    async def test_full_without_prior_preview_returns_error_string(
        self, tmp_path: Path
    ) -> None:
        """full=true without a prior preview returns an error message string."""
        content = "\n".join(f"line-{i}" for i in range(1, 21))
        _write(tmp_path / "nope.txt", content)
        tool = make_read_tool(tmp_path, preview_threshold=10, preview_lines=3)
        # Call with full=true directly (no preview first)
        result = await tool.execute({"path": "nope.txt", "full": True})
        # Returns an error string, not an exception
        assert "Cannot retrieve full content" in result
        assert "no preview was previously returned" in result

    @pytest.mark.asyncio
    async def test_read_empty_file(self, tmp_path: Path) -> None:
        """Reading an empty file returns empty string."""
        _write(tmp_path / "empty.txt", "")
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "empty.txt"})
        assert result == ""

    @pytest.mark.asyncio
    async def test_read_unicode_content(self, tmp_path: Path) -> None:
        """Reading a file with unicode characters works correctly."""
        _write(tmp_path / "unicode.txt", "cafe\nresidue\nnaiive")
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "unicode.txt"})
        assert "cafe" in result
        assert "naiive" in result

    @pytest.mark.asyncio
    async def test_read_offset_beyond_file_length(self, tmp_path: Path) -> None:
        """Offset beyond file length returns empty content."""
        _write(tmp_path / "short.txt", "one\ntwo\nthree")
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "short.txt", "offset": 100})
        assert result == ""

    @pytest.mark.asyncio
    async def test_read_line_number_right_aligned(self, tmp_path: Path) -> None:
        """Line numbers are right-aligned in cat -n format."""
        lines = "\n".join(f"x{i}" for i in range(1, 12))
        _write(tmp_path / "aligned.txt", lines)
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=20)
        result = await tool.execute({"path": "aligned.txt"})
        # Lines 1-9 should be right-aligned to width of "11"
        result_lines = result.split("\n")
        # First line: " 1\tx1" (space-padded to width 2)
        assert result_lines[0].startswith(" 1\t")
        # Line 10 no leading space
        assert result_lines[9].startswith("10\t")

    @pytest.mark.asyncio
    async def test_read_subdirectory_file(self, tmp_path: Path) -> None:
        """Reading a file in a subdirectory works."""
        _write(tmp_path / "sub" / "deep.txt", "nested content")
        tool = make_read_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"path": "sub/deep.txt"})
        assert "nested content" in result


# ---------------------------------------------------------------------------
# TestMakeListDirTool
# ---------------------------------------------------------------------------


class TestMakeListDirTool:
    """Tests for the list directory tool constructor."""

    @pytest.mark.asyncio
    async def test_list_flat_directory(self, tmp_path: Path) -> None:
        """Lists entries with type, size, and path."""
        _write(tmp_path / "file.txt", "hello")
        (tmp_path / "subdir").mkdir()
        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert "file\t5\tfile.txt" in result
        assert "dir\t-\tsubdir" in result

    @pytest.mark.asyncio
    async def test_list_recursive(self, tmp_path: Path) -> None:
        """Recursive listing includes entries in subdirectories."""
        (tmp_path / "a").mkdir()
        _write(tmp_path / "a" / "nested.txt", "data")
        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": ".", "recursive": True})
        assert "a" in result
        assert "nested.txt" in result

    @pytest.mark.asyncio
    async def test_list_pattern_filter(self, tmp_path: Path) -> None:
        """Pattern filter only returns matching entries."""
        _write(tmp_path / "yes.py", "code")
        _write(tmp_path / "no.txt", "text")
        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": ".", "pattern": "*.py"})
        assert "yes.py" in result
        assert "no.txt" not in result

    @pytest.mark.asyncio
    async def test_list_max_results_truncation(self, tmp_path: Path) -> None:
        """max_results caps output and adds truncation note."""
        for i in range(10):
            _write(tmp_path / f"f{i}.txt", "x")
        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": ".", "max_results": 3})
        assert "(truncated at 3 results)" in result

    @pytest.mark.asyncio
    async def test_list_empty_directory(self, tmp_path: Path) -> None:
        """Empty directory returns empty result."""
        (tmp_path / "empty").mkdir()
        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "empty"})
        assert result == ""

    @pytest.mark.asyncio
    async def test_list_path_escape_raises(self, tmp_path: Path) -> None:
        """Path escape raises ToolError."""
        tool = make_list_dir_tool(tmp_path)
        with pytest.raises(ToolError, match="escapes root boundary"):
            await tool.execute({"path": "../../"})

    @pytest.mark.asyncio
    async def test_list_non_directory_raises(self, tmp_path: Path) -> None:
        """Listing a file (not directory) raises ToolError."""
        _write(tmp_path / "afile.txt", "content")
        tool = make_list_dir_tool(tmp_path)
        with pytest.raises(ToolError, match="Not a directory"):
            await tool.execute({"path": "afile.txt"})

    @pytest.mark.asyncio
    async def test_list_entries_sorted(self, tmp_path: Path) -> None:
        """Entries are sorted by path."""
        _write(tmp_path / "c.txt", "x")
        _write(tmp_path / "a.txt", "x")
        _write(tmp_path / "b.txt", "x")
        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        lines = result.strip().split("\n")
        paths = [line.split("\t")[2] for line in lines]
        assert paths == sorted(paths)


# ---------------------------------------------------------------------------
# TestMakeGlobTool
# ---------------------------------------------------------------------------


class TestMakeGlobTool:
    """Tests for the glob tool constructor."""

    @pytest.mark.asyncio
    async def test_simple_glob(self, tmp_path: Path) -> None:
        """Simple glob matches files."""
        _write(tmp_path / "a.py", "code")
        _write(tmp_path / "b.py", "code")
        _write(tmp_path / "c.txt", "text")
        tool = make_glob_tool(tmp_path)
        result = await tool.execute({"pattern": "*.py"})
        assert "a.py" in result
        assert "b.py" in result
        assert "c.txt" not in result

    @pytest.mark.asyncio
    async def test_recursive_glob(self, tmp_path: Path) -> None:
        """Recursive glob (**/*.py) matches nested files."""
        _write(tmp_path / "top.py", "x")
        _write(tmp_path / "sub" / "deep.py", "x")
        tool = make_glob_tool(tmp_path)
        result = await tool.execute({"pattern": "**/*.py"})
        assert "top.py" in result
        assert "deep.py" in result

    @pytest.mark.asyncio
    async def test_no_matches(self, tmp_path: Path) -> None:
        """No matches returns 'No matches found.'."""
        tool = make_glob_tool(tmp_path)
        result = await tool.execute({"pattern": "*.xyz"})
        assert result == "No matches found."

    @pytest.mark.asyncio
    async def test_max_results_cap(self, tmp_path: Path) -> None:
        """max_results caps output."""
        for i in range(10):
            _write(tmp_path / f"f{i}.txt", "x")
        tool = make_glob_tool(tmp_path)
        result = await tool.execute({"pattern": "*.txt", "max_results": 3})
        lines = [x for x in result.strip().split("\n") if not x.startswith("(")]
        assert len(lines) == 3

    @pytest.mark.asyncio
    async def test_results_sorted(self, tmp_path: Path) -> None:
        """Results are sorted by path."""
        _write(tmp_path / "c.txt", "x")
        _write(tmp_path / "a.txt", "x")
        _write(tmp_path / "b.txt", "x")
        tool = make_glob_tool(tmp_path)
        result = await tool.execute({"pattern": "*.txt"})
        lines = result.strip().split("\n")
        assert lines == sorted(lines)

    @pytest.mark.asyncio
    async def test_glob_with_path_argument(self, tmp_path: Path) -> None:
        """Glob with explicit path argument searches within that directory."""
        _write(tmp_path / "sub" / "found.txt", "x")
        _write(tmp_path / "other.txt", "x")
        tool = make_glob_tool(tmp_path)
        result = await tool.execute({"pattern": "*.txt", "path": "sub"})
        assert "found.txt" in result
        assert "other.txt" not in result


# ---------------------------------------------------------------------------
# TestMakeGrepTool
# ---------------------------------------------------------------------------


class TestMakeGrepTool:
    """Tests for the grep tool constructor."""

    @pytest.mark.asyncio
    async def test_simple_pattern_match(self, tmp_path: Path) -> None:
        """Simple pattern match returns file:line:content format."""
        _write(tmp_path / "code.py", "def foo():\n    return 42\n")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "foo"})
        assert "code.py:1:def foo():" in result

    @pytest.mark.asyncio
    async def test_case_insensitive_search(self, tmp_path: Path) -> None:
        """Case insensitive search finds matches regardless of case."""
        _write(tmp_path / "text.txt", "Hello World\ngoodbye world\n")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute(
            {"pattern": "hello", "case_sensitive": False}
        )
        assert "Hello World" in result

    @pytest.mark.asyncio
    async def test_context_lines(self, tmp_path: Path) -> None:
        """Context lines are included around matches."""
        lines = "before1\nbefore2\nMATCH\nafter1\nafter2"
        _write(tmp_path / "ctx.txt", lines)
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute(
            {"pattern": "MATCH", "context_lines": 1}
        )
        assert "before2" in result
        assert "after1" in result

    @pytest.mark.asyncio
    async def test_files_only_mode(self, tmp_path: Path) -> None:
        """files_only mode returns just file paths."""
        _write(tmp_path / "a.txt", "needle here")
        _write(tmp_path / "b.txt", "nothing here")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute(
            {"pattern": "needle", "mode": "files_only"}
        )
        assert "a.txt" in result
        # files_only should not contain line numbers or content
        assert ":" not in result

    @pytest.mark.asyncio
    async def test_count_mode(self, tmp_path: Path) -> None:
        """count mode returns the total match count number."""
        _write(tmp_path / "rep.txt", "aaa\naaa\nbbb\naaa\n")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "aaa", "mode": "count"})
        assert result == "3"

    @pytest.mark.asyncio
    async def test_include_filter(self, tmp_path: Path) -> None:
        """include filter only searches matching filenames."""
        _write(tmp_path / "search.py", "target line")
        _write(tmp_path / "search.txt", "target line")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "target", "include": "*.py"})
        assert "search.py" in result
        assert "search.txt" not in result

    @pytest.mark.asyncio
    async def test_no_matches(self, tmp_path: Path) -> None:
        """No matches returns 'No matches found.'."""
        _write(tmp_path / "empty.txt", "nothing relevant here\n")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "zzzzzzz"})
        assert result == "No matches found."

    @pytest.mark.asyncio
    async def test_invalid_regex_raises(self, tmp_path: Path) -> None:
        """Invalid regex pattern raises ToolError."""
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        with pytest.raises(ToolError, match="Invalid regex pattern"):
            await tool.execute({"pattern": "[unclosed"})

    @pytest.mark.asyncio
    async def test_skips_hidden_directories(self, tmp_path: Path) -> None:
        """Hidden directories (starting with .) are skipped."""
        _write(tmp_path / ".hidden" / "secret.txt", "findme")
        _write(tmp_path / "visible" / "public.txt", "findme")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "findme"})
        assert "visible" in result
        assert ".hidden" not in result

    @pytest.mark.asyncio
    async def test_skips_binary_files(self, tmp_path: Path) -> None:
        """Binary files are skipped during grep."""
        _write_binary(tmp_path / "bin.dat", b"\x00findme\xff")
        _write(tmp_path / "text.txt", "findme here")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "findme"})
        assert "text.txt" in result
        assert "bin.dat" not in result

    @pytest.mark.asyncio
    async def test_grep_case_sensitive_default(self, tmp_path: Path) -> None:
        """Default case_sensitive=true does not match different case."""
        _write(tmp_path / "case.txt", "UPPER\nlower\n")
        tool = make_grep_tool(tmp_path, preview_threshold=10000, preview_lines=5)
        result = await tool.execute({"pattern": "upper"})
        assert result == "No matches found."


# ---------------------------------------------------------------------------
# TestMakeStatTool
# ---------------------------------------------------------------------------


class TestMakeStatTool:
    """Tests for the stat tool constructor."""

    @pytest.mark.asyncio
    async def test_file_stat_returns_all_fields(self, tmp_path: Path) -> None:
        """File stat returns all expected metadata fields."""
        _write(tmp_path / "info.py", "x = 1\ny = 2\n")
        tool = make_stat_tool(tmp_path)
        result = await tool.execute({"path": "info.py"})
        info = json.loads(result)
        assert info["exists"] is True
        assert info["byte_size"] > 0
        assert info["line_count"] == 2
        assert info["language"] == "python"
        assert info["mtime"] is not None
        assert info["binary"] is False

    @pytest.mark.asyncio
    async def test_nonexistent_file_stat(self, tmp_path: Path) -> None:
        """Non-existent file returns exists=false with null fields."""
        tool = make_stat_tool(tmp_path)
        result = await tool.execute({"path": "ghost.txt"})
        info = json.loads(result)
        assert info["exists"] is False
        assert info["byte_size"] is None
        assert info["line_count"] is None
        assert info["language"] is None
        assert info["mtime"] is None
        assert info["binary"] is None

    @pytest.mark.asyncio
    async def test_glob_pattern_returns_array(self, tmp_path: Path) -> None:
        """Glob pattern in stat returns an array of results."""
        _write(tmp_path / "a.py", "x")
        _write(tmp_path / "b.py", "y")
        tool = make_stat_tool(tmp_path)
        result = await tool.execute({"path": "*.py"})
        info = json.loads(result)
        assert isinstance(info, list)
        assert len(info) == 2
        paths = {item["path"] for item in info}
        assert "a.py" in paths
        assert "b.py" in paths

    @pytest.mark.asyncio
    async def test_binary_file_detection(self, tmp_path: Path) -> None:
        """Binary file is detected in stat output."""
        _write_binary(tmp_path / "bin.dat", b"\x00\x01\x02\xff")
        tool = make_stat_tool(tmp_path)
        result = await tool.execute({"path": "bin.dat"})
        info = json.loads(result)
        assert info["binary"] is True
        assert info["line_count"] is None

    @pytest.mark.asyncio
    async def test_stat_language_detection(self, tmp_path: Path) -> None:
        """Language is detected from file extension."""
        _write(tmp_path / "style.css", "body {}")
        tool = make_stat_tool(tmp_path)
        result = await tool.execute({"path": "style.css"})
        info = json.loads(result)
        assert info["language"] == "css"

    @pytest.mark.asyncio
    async def test_stat_unknown_extension(self, tmp_path: Path) -> None:
        """Unknown extension returns null language."""
        _write(tmp_path / "data.zzz", "stuff")
        tool = make_stat_tool(tmp_path)
        result = await tool.execute({"path": "data.zzz"})
        info = json.loads(result)
        assert info["language"] is None


# ---------------------------------------------------------------------------
# TestMakeDiffTool
# ---------------------------------------------------------------------------


class TestMakeDiffTool:
    """Tests for the diff tool constructor."""

    @pytest.mark.asyncio
    async def test_different_files_produce_diff(self, tmp_path: Path) -> None:
        """Different files produce unified diff output."""
        _write(tmp_path / "a.txt", "line1\nline2\nline3\n")
        _write(tmp_path / "b.txt", "line1\nchanged\nline3\n")
        tool = make_diff_tool(tmp_path)
        result = await tool.execute({"path_a": "a.txt", "path_b": "b.txt"})
        assert "---" in result
        assert "+++" in result
        assert "-line2" in result
        assert "+changed" in result

    @pytest.mark.asyncio
    async def test_identical_files(self, tmp_path: Path) -> None:
        """Identical files return 'Files are identical.'."""
        _write(tmp_path / "same1.txt", "identical content\n")
        _write(tmp_path / "same2.txt", "identical content\n")
        tool = make_diff_tool(tmp_path)
        result = await tool.execute({"path_a": "same1.txt", "path_b": "same2.txt"})
        assert result == "Files are identical."

    @pytest.mark.asyncio
    async def test_nonexistent_file_raises(self, tmp_path: Path) -> None:
        """Non-existent file raises ToolError."""
        _write(tmp_path / "exists.txt", "content")
        tool = make_diff_tool(tmp_path)
        with pytest.raises(ToolError, match="File not found"):
            await tool.execute({"path_a": "exists.txt", "path_b": "missing.txt"})

    @pytest.mark.asyncio
    async def test_binary_file_raises(self, tmp_path: Path) -> None:
        """Binary file raises ToolError with 'cannot diff' message."""
        _write_binary(tmp_path / "bin.dat", b"\x00\x01\x02\xff")
        _write(tmp_path / "text.txt", "normal text\n")
        tool = make_diff_tool(tmp_path)
        with pytest.raises(ToolError, match="Binary file, cannot diff"):
            await tool.execute({"path_a": "bin.dat", "path_b": "text.txt"})

    @pytest.mark.asyncio
    async def test_diff_uses_relative_labels(self, tmp_path: Path) -> None:
        """Diff header uses relative paths as labels."""
        _write(tmp_path / "sub" / "orig.txt", "old\n")
        _write(tmp_path / "sub" / "new.txt", "new\n")
        tool = make_diff_tool(tmp_path)
        result = await tool.execute(
            {"path_a": "sub/orig.txt", "path_b": "sub/new.txt"}
        )
        # Labels in unified diff header should be relative
        assert "sub/orig.txt" in result
        assert "sub/new.txt" in result

    @pytest.mark.asyncio
    async def test_diff_path_escape_raises(self, tmp_path: Path) -> None:
        """Path escape in diff raises ToolError."""
        _write(tmp_path / "ok.txt", "content")
        tool = make_diff_tool(tmp_path)
        with pytest.raises(ToolError, match="escapes root boundary"):
            await tool.execute({"path_a": "../evil.txt", "path_b": "ok.txt"})
