from __future__ import annotations

from typing import Any

from orxtra.protocols._tool import Tool
from orxtra.tool._validation import validate_args

_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "type": {
            "type": "string",
            "enum": ["learning", "decision", "issue"],
            "description": "The entry type.",
        },
        "text": {
            "type": "string",
            "minLength": 1,
            "description": "The entry text. Must be non-empty.",
        },
    },
    "required": ["type", "text"],
    "additionalProperties": False,
}

_DESCRIPTION = (
    "Write an entry to the run's shared notepad. Entries are visible to all"
    " agents in the run and persist across task boundaries. Use type"
    " 'learning' for insights discovered during work, 'decision' for choices"
    " made and their rationale, and 'issue' for problems that need attention."
)


def make_notepad_tool(
    trace_writer: Any,  # noqa: ANN401
    run_id: str,
    task_name: str,
    agent_name: str,
) -> Tool:
    """Create a notepad tool for recording entries to the shared notepad.

    Args:
        trace_writer: Writer instance with a write_notepad_entry method.
        run_id: The current run identifier.
        task_name: The current task name.
        agent_name: The agent recording the entry.

    Returns:
        A Tool instance for notepad operations.
    """

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _PARAMETERS)
        entry_type: str = arguments["type"]
        text: str = arguments["text"]
        await trace_writer.write_notepad_entry(
            run_id, task_name, agent_name, entry_type, text
        )
        return f"Notepad entry recorded (type={entry_type})."

    return Tool(
        name="notepad",
        description=_DESCRIPTION,
        parameters=_PARAMETERS,
        execute=execute,
    )
