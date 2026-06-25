from __future__ import annotations

from pathlib import Path as _Path
from typing import TYPE_CHECKING

from orxtra.overseer._tools import (
    make_add_constraint_tool,
    make_create_inbox_item_tool,
    make_record_assumption_tool,
    make_record_decision_tool,
    make_update_workflow_status_tool,
    make_write_lesson_tool,
)
from orxtra.overseer._format import format_event
from orxtra.protocols._overseer_protocols import OverseerEvent
from orxtra.tool._notepad_tool import make_notepad_tool
from orxtra.tool._read_tools import (
    make_diff_tool,
    make_glob_tool,
    make_grep_tool,
    make_list_dir_tool,
    make_read_tool,
    make_stat_tool,
)

if TYPE_CHECKING:
    from pathlib import Path
    from uuid import UUID

    from orxtra.overseer._autonomy import AutonomyLevel
    from orxtra.overseer._health import HealthMonitor
    from orxtra.protocols._tool import Tool
    from orxtra.session import Session
    from orxtra.trace import TraceWriter

_PROMPTS_DIR = _Path(__file__).resolve().parent / "prompts"


def load_overseer_prompt() -> str:
    """Load the Overseer's base system prompt from the prompts directory."""
    prompt_path = _PROMPTS_DIR / "overseer_base.md"
    return prompt_path.read_text(encoding="utf-8")


class Overseer:
    def __init__(  # noqa: PLR0913
        self,
        session: Session,
        trace_writer: TraceWriter,
        run_id: UUID,
        autonomy_level: AutonomyLevel,
        health_monitor: HealthMonitor,
        read_root: Path,
        extra_tools: list[Tool] | None = None,
    ) -> None:
        self._session = session
        self._trace_writer = trace_writer
        self._run_id = run_id
        self._autonomy_level = autonomy_level
        self._health_monitor = health_monitor
        self._read_root = read_root
        self._extra_tools = extra_tools

    def set_extra_tools(self, tools: list[Tool]) -> None:
        """Replace extra tools list.

        Used by the scheduler to inject lifecycle and consult tools after
        construction (the Overseer is created before the scheduler exists).
        """
        self._extra_tools = tools

    @property
    def session(self) -> Session:
        return self._session

    @session.setter
    def session(self, value: Session) -> None:
        self._session = value

    def prepare_event(self, event: OverseerEvent) -> str:
        return format_event(event)

    def get_tools(self) -> list[Tool]:
        memory_tools = [
            make_record_decision_tool(self._trace_writer, self._run_id),
            make_add_constraint_tool(self._trace_writer, self._run_id),
            make_record_assumption_tool(self._trace_writer, self._run_id),
            make_create_inbox_item_tool(
                self._trace_writer, self._run_id,
            ),
            make_write_lesson_tool(self._trace_writer, self._run_id),
            make_update_workflow_status_tool(self._trace_writer),
        ]
        file_tools = [
            make_read_tool(
                self._read_root,
                preview_threshold=10000,
                preview_lines=50,
            ),
            make_list_dir_tool(self._read_root),
            make_glob_tool(self._read_root),
            make_grep_tool(
                self._read_root,
                preview_threshold=10000,
                preview_lines=50,
            ),
            make_stat_tool(self._read_root),
            make_diff_tool(self._read_root),
        ]
        notepad_tools = [
            make_notepad_tool(
                self._trace_writer,
                str(self._run_id),
                "overseer",
                "overseer",
            ),
        ]
        return (
            memory_tools
            + file_tools
            + notepad_tools
            + list(self._extra_tools or [])
        )
