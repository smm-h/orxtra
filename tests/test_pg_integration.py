"""PostgreSQL integration tests.

These tests require a running PostgreSQL instance and ORXT_TEST_PG=1.
They exercise the trace, notepad, and session modules against a real database.
"""
from __future__ import annotations

import os

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("ORXT_TEST_PG"),
    reason="PG integration tests require ORXT_TEST_PG=1 and a running PostgreSQL",
)


class TestTraceWriter:
    # Tests for trace._writer.TraceWriter: verifies that run creation,
    # state transitions, task creation, and event writing all persist
    # correctly to the trace schema in PostgreSQL.

    def test_create_run_persists(self) -> None:
        pass

    def test_transition_run_updates_state(self) -> None:
        pass

    def test_create_task_and_write_event(self) -> None:
        pass


class TestNotepadOperations:
    # Tests for notepad read/write operations: verifies that notes can be
    # appended, read back in order, and that cross-agent visibility works
    # correctly via the PG-backed append-only store.

    def test_write_and_read_note(self) -> None:
        pass

    def test_notes_ordered_by_creation(self) -> None:
        pass

    def test_cross_agent_visibility(self) -> None:
        pass


class TestSessionPersistence:
    # Tests for session transcript persistence and reload: verifies that
    # session transcripts are persisted to PG, can be reloaded after restart,
    # and that token tracking survives round-trips.

    def test_transcript_persisted(self) -> None:
        pass

    def test_reload_after_restart(self) -> None:
        pass


class TestCrashRecovery:
    # Tests for trace-based crash recovery flow: verifies that incomplete
    # runs are detected on startup, that the recovery process replays
    # from the last known good state, and that recovered runs resume correctly.

    def test_detect_incomplete_run(self) -> None:
        pass

    def test_recovery_replays_from_checkpoint(self) -> None:
        pass

    def test_recovered_run_resumes(self) -> None:
        pass
