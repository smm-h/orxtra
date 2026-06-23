from __future__ import annotations

import pytest
import uuid6
from orxtra.scheduler._locks import FileLockRegistry


class TestFileLockRegistry:
    def test_claim_succeeds(self) -> None:
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/a/b.py", "/a/c.py"])
        assert registry.check_conflict(["/a/b.py"]) == wf_id

    def test_conflicting_claim_raises(self) -> None:
        registry = FileLockRegistry()
        wf1 = uuid6.uuid7()
        wf2 = uuid6.uuid7()
        registry.claim(wf1, ["/a/b.py"])
        try:
            registry.claim(wf2, ["/a/b.py"])
        except ValueError:
            pass
        else:
            pytest.fail("Expected ValueError for conflicting claim")

    def test_release_frees_paths(self) -> None:
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/a/b.py"])
        registry.release(wf_id)
        assert registry.check_conflict(["/a/b.py"]) is None

    def test_non_overlapping_claims_succeed(self) -> None:
        registry = FileLockRegistry()
        wf1 = uuid6.uuid7()
        wf2 = uuid6.uuid7()
        registry.claim(wf1, ["/a/b.py"])
        registry.claim(wf2, ["/c/d.py"])
        assert registry.check_conflict(["/a/b.py"]) == wf1
        assert registry.check_conflict(["/c/d.py"]) == wf2

    def test_release_nonexistent_no_error(self) -> None:
        registry = FileLockRegistry()
        registry.release(uuid6.uuid7())

    def test_claim_empty_paths(self) -> None:
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, [])
        assert registry.check_conflict([]) is None

    def test_exact_overlap_detected(self) -> None:
        """Identical paths conflict (existing behavior)."""
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/src/a/file.py"])
        assert registry.check_conflict(["/src/a/file.py"]) == wf_id

    def test_prefix_overlap_parent_claimed(self) -> None:
        """A claimed parent path conflicts with a child path."""
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/src/a"])
        assert registry.check_conflict(["/src/a/b/file.py"]) == wf_id

    def test_prefix_overlap_child_claimed(self) -> None:
        """A claimed child path conflicts with a parent path."""
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/src/a/b"])
        assert registry.check_conflict(["/src/a"]) == wf_id

    def test_non_overlapping_paths_no_conflict(self) -> None:
        """Paths with no prefix relationship don't conflict."""
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/src/a"])
        assert registry.check_conflict(["/src/b"]) is None

    def test_similar_prefix_no_false_positive(self) -> None:
        """/src/abc should not conflict with /src/a (not a true prefix)."""
        registry = FileLockRegistry()
        wf_id = uuid6.uuid7()
        registry.claim(wf_id, ["/src/a"])
        assert registry.check_conflict(["/src/abc"]) is None

    def test_prefix_overlap_claim_raises(self) -> None:
        """Claiming a nested path raises ValueError."""
        registry = FileLockRegistry()
        wf1 = uuid6.uuid7()
        wf2 = uuid6.uuid7()
        registry.claim(wf1, ["/src/a"])
        with pytest.raises(ValueError, match="conflict"):
            registry.claim(wf2, ["/src/a/b"])
