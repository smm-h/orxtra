from __future__ import annotations

import os
from pathlib import Path

import pytest
from orxt.tool._path import PathError, check_write_scope, resolve_and_check


class TestResolveAndCheck:
    """Tests for resolve_and_check."""

    def test_valid_relative_path(self, tmp_path: Path) -> None:
        """Relative path within root resolves correctly."""
        (tmp_path / "file.txt").touch()
        result = resolve_and_check("file.txt", tmp_path)
        assert result == tmp_path / "file.txt"

    def test_valid_nested_path(self, tmp_path: Path) -> None:
        """Nested relative path within root resolves correctly."""
        (tmp_path / "a" / "b").mkdir(parents=True)
        (tmp_path / "a" / "b" / "file.txt").touch()
        result = resolve_and_check("a/b/file.txt", tmp_path)
        assert result == tmp_path / "a" / "b" / "file.txt"

    def test_path_escape_via_dotdot(self, tmp_path: Path) -> None:
        """Path with ../ that escapes root raises PathError."""
        with pytest.raises(PathError, match="escapes root boundary"):
            resolve_and_check("../outside.txt", tmp_path)

    def test_absolute_path_outside_root(self, tmp_path: Path) -> None:
        """Absolute path outside root raises PathError."""
        with pytest.raises(PathError, match="escapes root boundary"):
            resolve_and_check("/etc/passwd", tmp_path)

    def test_absolute_path_inside_root(self, tmp_path: Path) -> None:
        """Absolute path inside root works."""
        (tmp_path / "file.txt").touch()
        abs_path = str(tmp_path / "file.txt")
        result = resolve_and_check(abs_path, tmp_path)
        assert result == tmp_path / "file.txt"

    def test_symlink_escape(self, tmp_path: Path) -> None:
        """Symlink pointing outside root raises PathError."""
        outside = tmp_path.parent / "outside_target"
        outside.mkdir(exist_ok=True)
        (outside / "secret.txt").touch()
        link = tmp_path / "sneaky_link"
        link.symlink_to(outside)
        try:
            with pytest.raises(PathError, match="escapes root boundary"):
                resolve_and_check("sneaky_link/secret.txt", tmp_path)
        finally:
            link.unlink()
            (outside / "secret.txt").unlink()
            outside.rmdir()

    def test_empty_path(self, tmp_path: Path) -> None:
        """Empty path raises PathError."""
        with pytest.raises(PathError, match="must not be empty"):
            resolve_and_check("", tmp_path)

    def test_path_equal_to_root(self, tmp_path: Path) -> None:
        """Path that resolves to root itself works."""
        result = resolve_and_check(".", tmp_path)
        assert result == tmp_path

    def test_dot_slash_prefix(self, tmp_path: Path) -> None:
        """Path with ./ prefix resolves correctly."""
        (tmp_path / "file.txt").touch()
        result = resolve_and_check("./file.txt", tmp_path)
        assert result == tmp_path / "file.txt"

    def test_multiple_dotdot_hops(self, tmp_path: Path) -> None:
        """Multiple ../ hops that escape root raises PathError."""
        with pytest.raises(PathError, match="escapes root boundary"):
            resolve_and_check("../../../../../../etc/passwd", tmp_path)

    def test_dotdot_that_stays_within_root(self, tmp_path: Path) -> None:
        """../ that stays within root works."""
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "b" / "file.txt").touch()
        result = resolve_and_check("a/../b/file.txt", tmp_path)
        assert result == tmp_path / "b" / "file.txt"

    def test_nonexistent_file_within_root(self, tmp_path: Path) -> None:
        """Non-existent file within root resolves (no existence check)."""
        result = resolve_and_check("does_not_exist.txt", tmp_path)
        assert result == tmp_path / "does_not_exist.txt"

    def test_root_with_trailing_sep(self, tmp_path: Path) -> None:
        """Root path behavior is correct regardless of how root is constructed."""
        root = Path(str(tmp_path) + os.sep)
        (tmp_path / "file.txt").touch()
        result = resolve_and_check("file.txt", root.resolve())
        assert result == tmp_path / "file.txt"

    def test_path_with_spaces(self, tmp_path: Path) -> None:
        """Paths with spaces resolve correctly."""
        (tmp_path / "my file.txt").touch()
        result = resolve_and_check("my file.txt", tmp_path)
        assert result == tmp_path / "my file.txt"

    def test_deeply_nested_path(self, tmp_path: Path) -> None:
        """Deeply nested path within root works."""
        deep = tmp_path / "a" / "b" / "c" / "d" / "e"
        deep.mkdir(parents=True)
        (deep / "file.txt").touch()
        result = resolve_and_check("a/b/c/d/e/file.txt", tmp_path)
        assert result == deep / "file.txt"


class TestCheckWriteScope:
    """Tests for check_write_scope."""

    def test_scope_none_unrestricted(self, tmp_path: Path) -> None:
        """None scope means unrestricted -- any path within root is ok."""
        resolved = tmp_path / "anywhere" / "file.txt"
        check_write_scope(resolved, None, tmp_path)  # Should not raise

    def test_path_within_scope(self, tmp_path: Path) -> None:
        """Path within a scope directory passes."""
        scope_dir = tmp_path / "allowed"
        scope_dir.mkdir()
        resolved = scope_dir / "file.txt"
        check_write_scope(resolved, [scope_dir], tmp_path)  # Should not raise

    def test_path_outside_scope(self, tmp_path: Path) -> None:
        """Path outside all scope directories raises PathError."""
        scope_dir = tmp_path / "allowed"
        scope_dir.mkdir()
        resolved = tmp_path / "forbidden" / "file.txt"
        with pytest.raises(PathError, match="outside write scope"):
            check_write_scope(resolved, [scope_dir], tmp_path)

    def test_multiple_scopes_match_any(self, tmp_path: Path) -> None:
        """Path matching any of multiple scope directories passes."""
        scope_a = tmp_path / "a"
        scope_b = tmp_path / "b"
        scope_a.mkdir()
        scope_b.mkdir()
        resolved = scope_b / "file.txt"
        check_write_scope(resolved, [scope_a, scope_b], tmp_path)  # Should not raise

    def test_subdirectory_of_scope(self, tmp_path: Path) -> None:
        """Path in a subdirectory of a scope directory passes."""
        scope_dir = tmp_path / "allowed"
        scope_dir.mkdir()
        (scope_dir / "sub").mkdir()
        resolved = scope_dir / "sub" / "file.txt"
        check_write_scope(resolved, [scope_dir], tmp_path)  # Should not raise

    def test_path_equal_to_scope(self, tmp_path: Path) -> None:
        """Path equal to a scope directory itself passes."""
        scope_dir = tmp_path / "allowed"
        scope_dir.mkdir()
        check_write_scope(scope_dir, [scope_dir], tmp_path)  # Should not raise

    def test_empty_scope_list(self, tmp_path: Path) -> None:
        """Empty scope list means nothing is allowed."""
        resolved = tmp_path / "file.txt"
        with pytest.raises(PathError, match="outside write scope"):
            check_write_scope(resolved, [], tmp_path)
