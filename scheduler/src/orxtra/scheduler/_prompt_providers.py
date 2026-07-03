"""Fragment providers for scheduler prompt assembly.

Each provider generates Fragment(s) for one layer of the assembled
agent prompt. Providers read from scheduler state and produce
content matching the exact format of the old inline string
construction, preserving golden-output equivalence.

Priority ordering (ascending = earlier in output):
  10 - task preamble (task ID + start_task instruction)
  20 - task prompt (the user's prompt text, after variable substitution)
  30 - constraints
  40 - notepad
  50 - lessons (verified)
  55 - lessons (stale)
  60 - prior failure context
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from orxtra.compose import Fragment

if TYPE_CHECKING:
    from orxtra.notepad import NotepadEntry

_PROMPTS_DIR = Path(__file__).parent / "prompts"

# Entry types and their display headers for notepad rendering
_ENTRY_TYPES = ("learning", "decision", "issue")
_TYPE_HEADERS = {
    "learning": "Learnings",
    "decision": "Decisions",
    "issue": "Issues",
}


def _load_template(name: str) -> str:
    """Load a .md template from the prompts/ directory."""
    return (_PROMPTS_DIR / f"{name}.md").read_text().rstrip("\n")


class TaskPreambleProvider:
    """Produces the task-ID preamble fragment.

    Context keys: task_id (str)
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:
        task_id = context.get("task_id")
        if task_id is None:
            return []
        template = _load_template("task_preamble")
        content = template.replace("{task_id}", str(task_id))
        return [
            Fragment(
                name="task_preamble",
                content=content,
                priority=10,
                source="scheduler:preamble",
            ),
        ]


class TaskPromptProvider:
    """Produces the task prompt fragment (after variable substitution).

    The task prompt is passed in via context["task_prompt"] already
    resolved (variables substituted by the caller before composition).
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:
        prompt = context.get("task_prompt")
        if prompt is None:
            return []
        return [
            Fragment(
                name="task_prompt",
                content=str(prompt),
                priority=20,
                source="scheduler:task_prompt",
            ),
        ]


class ConstraintsProvider:
    """Produces the active constraints fragment.

    Context keys: constraints (list of (text, tier) tuples)
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:
        constraints: list[tuple[str, str]] = context.get(
            "constraints", [],
        )
        if not constraints:
            return []
        header = _load_template("constraints")
        lines = [header]
        for text, tier in constraints:
            lines.append(f"- {text} ({tier})")
        return [
            Fragment(
                name="constraints",
                content="\n".join(lines),
                priority=30,
                source="scheduler:constraints",
            ),
        ]


class NotepadProvider:
    """Produces the notepad section fragment.

    Renders notepad entries grouped by type. The notepad module
    provides data-only APIs; all rendering lives here.

    Context keys: notepad_entries (list[NotepadEntry])
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:
        entries: list[NotepadEntry] = context.get(
            "notepad_entries", [],
        )
        if not entries:
            return []
        content = _render_notepad(entries)
        return [
            Fragment(
                name="notepad",
                content=content,
                priority=40,
                source="scheduler:notepad",
            ),
        ]


def _render_notepad(entries: list[NotepadEntry]) -> str:
    """Render notepad entries to markdown grouped by type."""
    groups: dict[str, list[NotepadEntry]] = {t: [] for t in _ENTRY_TYPES}
    for entry in entries:
        if entry.entry_type in groups:
            groups[entry.entry_type].append(entry)

    sections: list[str] = [
        _load_template("notepad"),
    ]
    for entry_type in _ENTRY_TYPES:
        header = _TYPE_HEADERS[entry_type]
        sections.append(f"\n### {header}")
        group = groups[entry_type]
        if group:
            sections.extend(
                f"- [{e.task_name}/{e.agent_name}] {e.text}"
                for e in group
            )
        else:
            sections.append("- (none)")

    return "\n".join(sections) + "\n"


class LessonsProvider:
    """Produces lesson fragments (verified and/or stale).

    Context keys: lessons (list of dicts with 'text' and 'stale' keys)
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:
        lessons: list[dict[str, Any]] = context.get("lessons", [])
        if not lessons:
            return []

        fresh = [l for l in lessons if not l.get("stale", False)]  # noqa: E741
        stale = [l for l in lessons if l.get("stale", False)]  # noqa: E741

        result: list[Fragment] = []
        if fresh:
            header = _load_template("lessons_verified")
            lines = [header]
            for lesson in fresh:
                lines.append(f"- {lesson['text']}")
            result.append(
                Fragment(
                    name="lessons_verified",
                    content="\n".join(lines),
                    priority=50,
                    source="scheduler:lessons",
                ),
            )
        if stale:
            header = _load_template("lessons_stale")
            lines = [header]
            for lesson in stale:
                lines.append(
                    f"- {lesson['text']}"
                    f" [stale: source modified"
                    f" after lesson was created]",
                )
            result.append(
                Fragment(
                    name="lessons_stale",
                    content="\n".join(lines),
                    priority=55,
                    source="scheduler:lessons",
                ),
            )
        return result


class FailureContextProvider:
    """Produces the prior failure context fragment.

    Context keys:
      attempt (int) - current attempt number
      retry_inject_failure (bool) - whether to inject failure context
      prior_attempts (list of dicts with 'attempt' and 'error' keys)
    """

    def fragments(self, context: dict[str, Any]) -> list[Fragment]:
        attempt = context.get("attempt", 1)
        inject = context.get("retry_inject_failure", False)
        prior: list[dict[str, Any]] = context.get(
            "prior_attempts", [],
        )
        if attempt <= 1 or not inject or not prior:
            return []

        header = _load_template("prior_failures")
        lines = [header]
        for pa in prior:
            lines.append(
                f"Prior attempt {pa['attempt']}"
                f" failed: {pa['error']}",
            )
        return [
            Fragment(
                name="prior_failures",
                content="\n".join(lines),
                priority=60,
                source="scheduler:failures",
            ),
        ]
