from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from orxt.notepad import NotepadEntry, format_notepad

RUN_ID = UUID("01234567-89ab-cdef-0123-456789abcdef")
NOW = datetime(2025, 6, 1, 12, 0, 0, tzinfo=UTC)


def _entry(
    entry_type: str,
    text: str,
    task_name: str = "task1",
    agent_name: str = "agent1",
) -> NotepadEntry:
    return NotepadEntry(
        run_id=RUN_ID,
        task_name=task_name,
        agent_name=agent_name,
        entry_type=entry_type,
        text=text,
        created_at=NOW,
    )


class TestFormatNotepad:
    def test_groups_by_type(self) -> None:
        entries = [
            _entry("learning", "learned A"),
            _entry("decision", "decided B", task_name="task2", agent_name="agent2"),
            _entry("issue", "issue C", task_name="task3", agent_name="agent3"),
        ]

        result = format_notepad(entries)

        assert "### Learnings" in result
        assert "### Decisions" in result
        assert "### Issues" in result
        assert "- [task1/agent1] learned A" in result
        assert "- [task2/agent2] decided B" in result
        assert "- [task3/agent3] issue C" in result
        assert "(none)" not in result

    def test_empty_entries_shows_all_none(self) -> None:
        result = format_notepad([])

        assert "## Context from previous steps" in result
        assert "### Learnings" in result
        assert "### Decisions" in result
        assert "### Issues" in result
        assert result.count("(none)") == 3

    def test_only_learnings_shows_none_for_others(self) -> None:
        entries = [_entry("learning", "learned A")]

        result = format_notepad(entries)

        assert "- [task1/agent1] learned A" in result
        learnings_pos = result.index("### Learnings")
        decisions_pos = result.index("### Decisions")
        issues_pos = result.index("### Issues")
        assert learnings_pos < decisions_pos < issues_pos
        # Decisions and Issues should show (none)
        decisions_section = result[decisions_pos:issues_pos]
        issues_section = result[issues_pos:]
        assert "(none)" in decisions_section
        assert "(none)" in issues_section
        # Learnings section should not have (none)
        learnings_section = result[learnings_pos:decisions_pos]
        assert "(none)" not in learnings_section

    def test_multiple_entries_per_type(self) -> None:
        entries = [
            _entry("learning", "learned A"),
            _entry("learning", "learned B"),
            _entry("learning", "learned C"),
        ]

        result = format_notepad(entries)

        assert "- [task1/agent1] learned A" in result
        assert "- [task1/agent1] learned B" in result
        assert "- [task1/agent1] learned C" in result

    def test_entry_prefix_format(self) -> None:
        entries = [
            _entry(
                "decision", "chose X", task_name="research", agent_name="researcher",
            ),
        ]

        result = format_notepad(entries)

        assert "- [research/researcher] chose X" in result

    def test_preserves_order_within_group(self) -> None:
        entries = [
            _entry("learning", "first"),
            _entry("learning", "second"),
            _entry("learning", "third"),
        ]

        result = format_notepad(entries)

        first_pos = result.index("first")
        second_pos = result.index("second")
        third_pos = result.index("third")
        assert first_pos < second_pos < third_pos

    def test_all_three_types_populated(self) -> None:
        entries = [
            _entry("learning", "L1"),
            _entry("decision", "D1"),
            _entry("issue", "I1"),
        ]

        result = format_notepad(entries)

        assert "(none)" not in result
        assert "### Learnings" in result
        assert "### Decisions" in result
        assert "### Issues" in result

    def test_section_header(self) -> None:
        entries = [_entry("learning", "L1")]

        result = format_notepad(entries)

        assert result.startswith("## Context from previous steps")

    def test_unknown_entry_type_dropped(self) -> None:
        entries = [
            _entry("warning", "something bad"),
            _entry("learning", "learned A"),
        ]

        result = format_notepad(entries)

        assert "something bad" not in result
        assert "- [task1/agent1] learned A" in result

    def test_trailing_newline(self) -> None:
        entries = [_entry("learning", "learned A")]

        result = format_notepad(entries)

        assert result.endswith("\n")
