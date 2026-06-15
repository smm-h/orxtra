from __future__ import annotations

import pytest
import uuid6
from orxt.scheduler._locks import FileLockRegistry


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
