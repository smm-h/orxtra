from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest
from orxt.write_safety import StaleWriteError, StaleWriteTracker, compute_hash

if TYPE_CHECKING:
    from pathlib import Path


def test_read_then_write_same_hash(tmp_path: Path) -> None:
    tracker = StaleWriteTracker()
    f = tmp_path / "f.txt"
    f.write_text("content")
    h = compute_hash(f)
    tracker.record_read("s1", f, h)
    tracker.check_write("s1", f, h)


def test_write_without_read_raises(tmp_path: Path) -> None:
    tracker = StaleWriteTracker()
    f = tmp_path / "f.txt"
    f.write_text("content")
    h = compute_hash(f)
    with pytest.raises(StaleWriteError, match="has never read"):
        tracker.check_write("s1", f, h)


def test_write_after_change_raises(tmp_path: Path) -> None:
    tracker = StaleWriteTracker()
    f = tmp_path / "f.txt"
    f.write_text("original")
    h_old = compute_hash(f)
    tracker.record_read("s1", f, h_old)
    f.write_text("modified")
    h_new = compute_hash(f)
    with pytest.raises(StaleWriteError, match="changed since session"):
        tracker.check_write("s1", f, h_new)


def test_new_file_skips_check(tmp_path: Path) -> None:
    tracker = StaleWriteTracker()
    f = tmp_path / "new.txt"
    tracker.check_write("s1", f, "anyhash", is_new_file=True)


def test_multiple_sessions_independent(tmp_path: Path) -> None:
    tracker = StaleWriteTracker()
    f = tmp_path / "f.txt"
    f.write_text("content")
    h = compute_hash(f)
    tracker.record_read("s1", f, h)
    tracker.record_read("s2", f, h)
    tracker.check_write("s1", f, h)
    tracker.check_write("s2", f, h)


def test_cross_session_stale(tmp_path: Path) -> None:
    tracker = StaleWriteTracker()
    f = tmp_path / "f.txt"
    f.write_text("v1")
    h1 = compute_hash(f)
    tracker.record_read("s1", f, h1)
    tracker.record_read("s2", f, h1)
    # s1 writes, changing the hash
    f.write_text("v2")
    h2 = compute_hash(f)
    tracker.check_write("s1", f, h1)  # s1 still sees h1 -- pass
    # Now s2 tries to write but file has changed
    with pytest.raises(StaleWriteError, match="changed since session"):
        tracker.check_write("s2", f, h2)


def test_compute_hash_sha256(tmp_path: Path) -> None:
    f = tmp_path / "f.txt"
    f.write_bytes(b"hello world")
    expected = hashlib.sha256(b"hello world").hexdigest()
    assert compute_hash(f) == expected


def test_path_canonicalization_symlink(tmp_path: Path) -> None:
    real = tmp_path / "real.txt"
    real.write_text("content")
    link = tmp_path / "link.txt"
    link.symlink_to(real)
    tracker = StaleWriteTracker()
    h = compute_hash(real)
    tracker.record_read("s1", real, h)
    tracker.check_write("s1", link, h)
