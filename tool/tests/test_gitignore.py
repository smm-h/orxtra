"""Tests for .gitignore filtering in list_dir tool."""
from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from orxtra.tool._read_tools import make_list_dir_tool

if TYPE_CHECKING:
    from pathlib import Path


def _write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


class TestGitignoreFiltering:
    @pytest.mark.asyncio
    async def test_pycache_excluded(self, tmp_path: Path) -> None:
        """__pycache__/ pattern excludes __pycache__ directories."""
        _write(tmp_path / ".gitignore", "__pycache__/\n")
        (tmp_path / "__pycache__").mkdir()
        _write(tmp_path / "__pycache__" / "mod.cpython-312.pyc", "bytecode")
        _write(tmp_path / "main.py", "print('hello')")

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert "__pycache__" not in result
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_wildcard_extension_excluded(self, tmp_path: Path) -> None:
        """*.pyc pattern excludes .pyc files."""
        _write(tmp_path / ".gitignore", "*.pyc\n")
        _write(tmp_path / "mod.pyc", "bytecode")
        _write(tmp_path / "main.py", "print('hello')")

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert "mod.pyc" not in result
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_no_gitignore_lists_everything(self, tmp_path: Path) -> None:
        """Without .gitignore, all entries are listed."""
        _write(tmp_path / "a.py", "a")
        _write(tmp_path / "b.pyc", "b")
        (tmp_path / "__pycache__").mkdir()

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert "a.py" in result
        assert "b.pyc" in result
        assert "__pycache__" in result

    @pytest.mark.asyncio
    async def test_recursive_gitignore_filtering(self, tmp_path: Path) -> None:
        """Gitignore filtering works in recursive mode."""
        _write(tmp_path / ".gitignore", "*.pyc\n__pycache__/\n")
        (tmp_path / "src").mkdir()
        _write(tmp_path / "src" / "main.py", "code")
        _write(tmp_path / "src" / "main.pyc", "bytecode")
        (tmp_path / "src" / "__pycache__").mkdir()
        _write(tmp_path / "src" / "__pycache__" / "x.pyc", "bc")

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": ".", "recursive": True})
        assert "main.py" in result
        assert "main.pyc" not in result
        assert "__pycache__" not in result

    @pytest.mark.asyncio
    async def test_negation_pattern(self, tmp_path: Path) -> None:
        """Negation pattern !important.pyc keeps the file."""
        _write(tmp_path / ".gitignore", "*.pyc\n!important.pyc\n")
        _write(tmp_path / "mod.pyc", "bytecode")
        _write(tmp_path / "important.pyc", "important")
        _write(tmp_path / "main.py", "code")

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert "mod.pyc" not in result
        assert "important.pyc" in result
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_comments_and_blank_lines(self, tmp_path: Path) -> None:
        """Comments and blank lines in .gitignore are ignored."""
        _write(
            tmp_path / ".gitignore",
            "# This is a comment\n\n*.log\n\n# Another comment\n",
        )
        _write(tmp_path / "app.log", "log data")
        _write(tmp_path / "main.py", "code")

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert "app.log" not in result
        assert "main.py" in result

    @pytest.mark.asyncio
    async def test_gitignore_itself_visible(self, tmp_path: Path) -> None:
        """.gitignore file itself is visible in listing (unless explicitly ignored)."""
        _write(tmp_path / ".gitignore", "*.pyc\n")
        _write(tmp_path / "main.py", "code")

        tool = make_list_dir_tool(tmp_path)
        result = await tool.execute({"path": "."})
        assert ".gitignore" in result
