from __future__ import annotations

from typing import Any, Protocol

from orxt.protocols._tool import Tool
from orxt.tool._validation import validate_args


class TaskSchedulerRef(Protocol):
    """All task_id parameters are UUID-formatted strings.

    The scheduler converts to UUID internally.
    """

    async def handle_start_task(self, session_id: str, task_id: str) -> str: ...
    async def handle_end_task(self, session_id: str, message: str) -> str: ...
    async def handle_create_task(
        self, session_id: str, params: dict[str, Any]
    ) -> str: ...
    async def handle_create_workflow(
        self, session_id: str, params: dict[str, Any]
    ) -> str: ...
    async def handle_create_wait_for(
        self, session_id: str, params: dict[str, Any]
    ) -> str: ...
    async def handle_await_task(self, session_id: str, task_id: str) -> str: ...


# -- start_task --

_START_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "The task to enter"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def make_start_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that enters a task, triggering its pre-checks."""

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _START_TASK_PARAMETERS)
        return await scheduler_ref.handle_start_task(
            session_id, arguments["task_id"]
        )

    return Tool(
        name="start_task",
        description=(
            "Enter a task, triggering its pre-checks."
            " The task must exist and be in a startable state."
        ),
        parameters=_START_TASK_PARAMETERS,
        execute=execute,
    )


# -- end_task --

_END_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "message": {
            "type": "string",
            "description": (
                "What the agent accomplished."
                " Also used as commit message if file changes were made."
            ),
        },
    },
    "required": ["message"],
    "additionalProperties": False,
}


def make_end_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that completes the active task with a summary message."""

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _END_TASK_PARAMETERS)
        return await scheduler_ref.handle_end_task(
            session_id, arguments["message"]
        )

    return Tool(
        name="end_task",
        description=(
            "Complete the active task with a summary message."
            " Triggers post-checks for verification."
        ),
        parameters=_END_TASK_PARAMETERS,
        execute=execute,
    )


# -- create_task --

_CREATE_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Task name"},
        "agent": {
            "type": "string",
            "description": "Agent definition to execute this task",
        },
        "task_prompt": {
            "type": "string",
            "description": "Prompt describing what the task should accomplish",
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "description": "Timeout in seconds",
        },
        "context_refinement": {
            "type": "boolean",
            "description": "Whether to refine context before execution",
        },
        "prechecks": {
            "type": "array",
            "description": "Pre-check executions to run before the task",
        },
        "postchecks": {
            "type": "array",
            "description": "Post-check executions to run after the task",
        },
        "variable_values": {
            "type": "object",
            "description": "Variable substitutions for the task prompt",
        },
        "budget": {
            "type": "number",
            "minimum": 0,
            "description": "Budget in USD for this task",
        },
        "write_paths": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Paths this task is allowed to write to",
        },
        "category": {
            "type": "string",
            "description": "Task category for agent resolution",
        },
        "retry": {
            "type": "integer",
            "minimum": 0,
            "description": "Number of retry attempts on failure",
        },
        "retry_resume": {
            "type": "boolean",
            "description": "Whether retries resume from failure point",
        },
        "retry_inject_failure": {
            "type": "boolean",
            "description": "Whether to inject failure context on retry",
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Task IDs this task depends on",
        },
    },
    "required": ["name", "agent", "task_prompt", "timeout", "context_refinement"],
    "additionalProperties": False,
}


def make_create_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that creates a concrete subtask within the active task."""

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _CREATE_TASK_PARAMETERS)
        return await scheduler_ref.handle_create_task(session_id, arguments)

    return Tool(
        name="create_task",
        description=(
            "Create a concrete subtask within the current active task."
            " The subtask will be scheduled for execution by its assigned agent."
        ),
        parameters=_CREATE_TASK_PARAMETERS,
        execute=execute,
    )


# -- create_workflow --

_CREATE_WORKFLOW_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Workflow name"},
        "description": {
            "type": "string",
            "description": "What this workflow accomplishes",
        },
        "goals": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 1,
            "description": "Goals the workflow must achieve",
        },
        "postchecks": {
            "type": "array",
            "description": "Post-check executions to run after the workflow",
        },
        "budget": {
            "type": "number",
            "minimum": 0,
            "description": "Budget in USD for this workflow",
        },
    },
    "required": ["name", "description", "goals"],
    "additionalProperties": False,
}


def make_create_workflow_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that creates a goal-oriented task tree."""

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _CREATE_WORKFLOW_PARAMETERS)
        return await scheduler_ref.handle_create_workflow(
            session_id, arguments
        )

    return Tool(
        name="create_workflow",
        description=(
            "Create a goal-oriented task tree within the current active task."
            " The workflow decomposes goals into subtasks."
        ),
        parameters=_CREATE_WORKFLOW_PARAMETERS,
        execute=execute,
    )


# -- create_wait_for --

_CREATE_WAIT_FOR_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "description": "Wait-for task name"},
        "event_name": {
            "type": "string",
            "description": "Name of the event to wait for",
        },
        "timeout": {
            "type": "integer",
            "minimum": 1,
            "description": "Timeout in seconds before the wait expires",
        },
        "depends_on": {
            "type": "array",
            "items": {"type": "string"},
            "description": "Task IDs this wait-for depends on",
        },
    },
    "required": ["name", "event_name", "timeout"],
    "additionalProperties": False,
}


def make_create_wait_for_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that blocks until a named event fires or timeout expires."""

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _CREATE_WAIT_FOR_PARAMETERS)
        return await scheduler_ref.handle_create_wait_for(
            session_id, arguments
        )

    return Tool(
        name="create_wait_for",
        description=(
            "Create a wait-for task that blocks until a named event fires"
            " or the timeout expires."
        ),
        parameters=_CREATE_WAIT_FOR_PARAMETERS,
        execute=execute,
    )


# -- await_task --

_AWAIT_TASK_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "task_id": {"type": "string", "description": "The task to await"},
    },
    "required": ["task_id"],
    "additionalProperties": False,
}


def make_await_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that suspends the session until a child task completes."""

    async def execute(arguments: dict[str, Any]) -> str:
        validate_args(arguments, _AWAIT_TASK_PARAMETERS)
        return await scheduler_ref.handle_await_task(
            session_id, arguments["task_id"]
        )

    return Tool(
        name="await_task",
        description=(
            "Suspend the current session until the specified child task completes."
            " The session will resume with the child's result."
        ),
        parameters=_AWAIT_TASK_PARAMETERS,
        execute=execute,
        suspending=True,
    )
