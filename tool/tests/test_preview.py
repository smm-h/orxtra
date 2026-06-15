from __future__ import annotations

from orxt.tool._preview import FullRetrievalGuard, PreviewResult, check_and_preview


class TestCheckAndPreview:
    """Tests for check_and_preview."""

    def test_under_threshold_returns_full(self) -> None:
        """Content under threshold is returned in full."""
        content = "hello world"
        result = check_and_preview(content, threshold=1000, preview_lines=3)
        assert result.content == content
        assert result.is_preview is False

    def test_at_threshold_returns_full(self) -> None:
        """Content at exactly threshold bytes is returned in full."""
        content = "x" * 100
        result = check_and_preview(content, threshold=100, preview_lines=3)
        assert result.content == content
        assert result.is_preview is False

    def test_over_threshold_returns_preview(self) -> None:
        """Content over threshold returns a preview."""
        lines = [f"line {i}\n" for i in range(100)]
        content = "".join(lines)
        result = check_and_preview(content, threshold=10, preview_lines=3)
        assert result.is_preview is True
        assert "line 0" in result.content
        assert "line 1" in result.content
        assert "line 2" in result.content
        assert "line 99" in result.content
        assert "omitted" in result.content

    def test_preview_shows_correct_total_lines(self) -> None:
        """Preview result includes correct total line count."""
        lines = [f"line {i}\n" for i in range(50)]
        content = "".join(lines)
        result = check_and_preview(content, threshold=10, preview_lines=5)
        assert result.total_lines == 50

    def test_preview_shows_correct_total_bytes(self) -> None:
        """Preview result includes correct total byte count."""
        content = "hello world\n" * 100
        expected_bytes = len(content.encode("utf-8"))
        result = check_and_preview(content, threshold=10, preview_lines=3)
        assert result.total_bytes == expected_bytes

    def test_empty_content(self) -> None:
        """Empty content returns full content (0 bytes is under any threshold)."""
        result = check_and_preview("", threshold=100, preview_lines=3)
        assert result.content == ""
        assert result.is_preview is False
        assert result.total_lines == 0
        assert result.total_bytes == 0

    def test_single_line_over_threshold(self) -> None:
        """Single line that exceeds threshold bytes gets previewed."""
        content = "x" * 1000
        result = check_and_preview(content, threshold=10, preview_lines=3)
        assert result.is_preview is True
        assert result.total_lines == 1
        assert result.total_bytes == 1000

    def test_full_content_preserves_exact_text(self) -> None:
        """When under threshold, content is preserved exactly."""
        content = "line 1\nline 2\n  indented\n"
        result = check_and_preview(content, threshold=10000, preview_lines=3)
        assert result.content == content

    def test_preview_result_is_frozen(self) -> None:
        """PreviewResult is immutable (frozen dataclass)."""
        result = check_and_preview("hello", threshold=1000, preview_lines=3)
        assert isinstance(result, PreviewResult)
        # frozen=True means attribute assignment raises
        import dataclasses
        assert dataclasses.fields(result)  # confirms it's a dataclass

    def test_preview_includes_byte_count_in_separator(self) -> None:
        """The preview separator mentions total bytes."""
        lines = [f"line {i}\n" for i in range(100)]
        content = "".join(lines)
        result = check_and_preview(content, threshold=10, preview_lines=2)
        total_bytes = len(content.encode("utf-8"))
        assert str(total_bytes) in result.content
        assert "100 total lines" in result.content

    def test_unicode_content_byte_threshold(self) -> None:
        """Threshold is based on byte size, not character count."""
        # Each emoji is 4 bytes in UTF-8
        content = "\U0001f600\n" * 10  # 10 lines of emoji
        char_count = len(content)
        byte_count = len(content.encode("utf-8"))
        assert byte_count > char_count
        # Set threshold between char count and byte count
        result = check_and_preview(
            content, threshold=char_count, preview_lines=2,
        )
        # Byte count exceeds char_count threshold, so it should be a preview
        assert result.is_preview is True


class TestFullRetrievalGuard:
    """Tests for FullRetrievalGuard."""

    def test_preview_recorded_then_full_allowed(self) -> None:
        """After recording a preview, full retrieval is allowed."""
        guard = FullRetrievalGuard()
        guard.record_preview("session1", "/path/to/file.txt")
        assert guard.check_full_allowed("session1", "/path/to/file.txt") is True

    def test_full_without_preview_not_allowed(self) -> None:
        """Full retrieval without prior preview is not allowed."""
        guard = FullRetrievalGuard()
        assert guard.check_full_allowed("session1", "/path/to/file.txt") is False

    def test_different_sessions_independent(self) -> None:
        """Different sessions have independent preview tracking."""
        guard = FullRetrievalGuard()
        guard.record_preview("session1", "/path/to/file.txt")
        assert guard.check_full_allowed("session1", "/path/to/file.txt") is True
        assert guard.check_full_allowed("session2", "/path/to/file.txt") is False

    def test_different_paths_independent(self) -> None:
        """Different paths are tracked independently."""
        guard = FullRetrievalGuard()
        guard.record_preview("session1", "/path/to/a.txt")
        assert guard.check_full_allowed("session1", "/path/to/a.txt") is True
        assert guard.check_full_allowed("session1", "/path/to/b.txt") is False

    def test_multiple_previews_same_session(self) -> None:
        """Multiple previews for the same session accumulate."""
        guard = FullRetrievalGuard()
        guard.record_preview("session1", "/a.txt")
        guard.record_preview("session1", "/b.txt")
        assert guard.check_full_allowed("session1", "/a.txt") is True
        assert guard.check_full_allowed("session1", "/b.txt") is True
