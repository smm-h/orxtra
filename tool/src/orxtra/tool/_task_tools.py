from __future__ import annotations

from typing import Any, Protocol

from orxtra.protocols._results import TaskLifecycleResult
from orxtra.protocols._tool import Tool
from orxtra.tool._decorator import tool
from orxtra.tool._params import (
    AwaitTaskParams,
    CreateTaskParams,
    CreateWaitForParams,
    CreateWorkflowParams,
    EndTaskParams,
    StartTaskParams,
)
from orxtra.tool._renderers import TextRenderer


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


@tool(
    "start_task",
    "Enter a task, triggering its pre-checks."
    " The task must exist and be in a startable state.",
    renderer=TextRenderer(),
    namespace="task.lifecycle",
    tags=frozenset({"lifecycle"}),
)
async def _start_task_impl(
    params: StartTaskParams,
    *,
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> TaskLifecycleResult:
    text = await scheduler_ref.handle_start_task(session_id, params.task_id)
    return TaskLifecycleResult(
        message=text, task_id=params.task_id, details=None,
    )


def make_start_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that enters a task, triggering its pre-checks."""
    return _start_task_impl.bind(
        scheduler_ref=scheduler_ref, session_id=session_id,
    )


# -- end_task --


@tool(
    "end_task",
    "Complete the active task with a summary message."
    " Triggers post-checks for verification.",
    renderer=TextRenderer(),
    namespace="task.lifecycle",
    tags=frozenset({"lifecycle"}),
)
async def _end_task_impl(
    params: EndTaskParams,
    *,
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> TaskLifecycleResult:
    text = await scheduler_ref.handle_end_task(session_id, params.message)
    return TaskLifecycleResult(message=text, task_id=None, details=None)


def make_end_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that completes the active task with a summary message."""
    return _end_task_impl.bind(
        scheduler_ref=scheduler_ref, session_id=session_id,
    )


# -- create_task --


@tool(
    "create_task",
    "Create a concrete subtask within the current active task."
    " The subtask will be scheduled for execution by its assigned agent.",
    renderer=TextRenderer(),
    namespace="task.lifecycle",
    tags=frozenset({"lifecycle"}),
)
async def _create_task_impl(
    params: CreateTaskParams,
    *,
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> TaskLifecycleResult:
    # The scheduler expects the full args dict (including only present fields).
    args = params.model_dump(exclude_none=True)
    text = await scheduler_ref.handle_create_task(session_id, args)
    return TaskLifecycleResult(message=text, task_id=text, details=None)


def make_create_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that creates a concrete subtask within the active task."""
    return _create_task_impl.bind(
        scheduler_ref=scheduler_ref, session_id=session_id,
    )


# -- create_workflow --


@tool(
    "create_workflow",
    "Create a goal-oriented task tree within the current active task."
    " The workflow decomposes goals into subtasks.",
    renderer=TextRenderer(),
    namespace="task.lifecycle",
    tags=frozenset({"lifecycle"}),
)
async def _create_workflow_impl(
    params: CreateWorkflowParams,
    *,
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> TaskLifecycleResult:
    args = params.model_dump(exclude_none=True)
    text = await scheduler_ref.handle_create_workflow(session_id, args)
    return TaskLifecycleResult(message=text, task_id=text, details=None)


def make_create_workflow_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that creates a goal-oriented task tree."""
    return _create_workflow_impl.bind(
        scheduler_ref=scheduler_ref, session_id=session_id,
    )


# -- create_wait_for --


@tool(
    "create_wait_for",
    "Create a wait-for task that blocks until a named event fires"
    " or the timeout expires.",
    renderer=TextRenderer(),
    namespace="task.lifecycle",
    tags=frozenset({"lifecycle"}),
)
async def _create_wait_for_impl(
    params: CreateWaitForParams,
    *,
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> TaskLifecycleResult:
    args = params.model_dump(exclude_none=True)
    text = await scheduler_ref.handle_create_wait_for(session_id, args)
    return TaskLifecycleResult(message=text, task_id=text, details=None)


def make_create_wait_for_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that blocks until a named event fires or timeout expires."""
    return _create_wait_for_impl.bind(
        scheduler_ref=scheduler_ref, session_id=session_id,
    )


# -- await_task --


@tool(
    "await_task",
    "Suspend the current session until the specified child task completes."
    " The session will resume with the child's result.",
    renderer=TextRenderer(),
    suspending=True,
    namespace="task.lifecycle",
    tags=frozenset({"lifecycle", "suspending"}),
)
async def _await_task_impl(
    params: AwaitTaskParams,
    *,
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> TaskLifecycleResult:
    text = await scheduler_ref.handle_await_task(session_id, params.task_id)
    return TaskLifecycleResult(
        message=text, task_id=params.task_id, details=None,
    )


def make_await_task_tool(
    scheduler_ref: TaskSchedulerRef,
    session_id: str,
) -> Tool:
    """Create a tool that suspends the session until a child task completes."""
    return _await_task_impl.bind(
        scheduler_ref=scheduler_ref, session_id=session_id,
    )
