"""Pipeline split for remote tool execution.

The local pipeline (tool/_pipeline.py) runs all 7 steps in-process.
For remote execution, steps are split between brain and worker:

  Brain: scheduler_check, secret substitution, send to worker,
         secret scrubbing, mutation recording, trace callback.
  Worker: actual tool execution (with transient retry).

The brain never retries -- if the worker returns an error, it is real.
"""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from orxtra.protocols import Tool, ToolLocation, ToolOutput
from orxtra.tool import scrub_data, scrub_text
from orxtra.worker._protocol import ExecuteToolCall, ToolCallResult

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from uuid import UUID

    from orxtra.secrets import SecretRegistry


def wrap_tool_for_remote(
    tool: Tool,
    send_to_worker_fn: Callable[[ExecuteToolCall], Awaitable[ToolCallResult]],
    secret_registry: SecretRegistry | None,
    scheduler_check: Callable[[str], UUID],
    trace_callback: Callable[..., Any] | None,
    mutation_tracker: dict[str, set[str]] | None,
    session_id: str,
    is_start_task: bool = False,
) -> Tool:
    """Wrap a tool so it executes remotely on a worker.

    Returns a new Tool with the same schema but an execute function
    that routes through the brain-worker protocol.
    """

    async def remote_execute(args: dict[str, Any]) -> ToolOutput[Any]:
        # Step 1 (brain): active task check.
        if not is_start_task:
            scheduler_check(session_id)

        # Step 2 (brain): secret substitution.
        if secret_registry is not None:
            serialized = json.dumps(args)
            substituted = secret_registry.substitute(serialized)
            effective_args: dict[str, Any] = json.loads(substituted)
        else:
            effective_args = args

        # Step 3 (worker): send to worker and await result.
        call = ExecuteToolCall(
            call_id=uuid4(),
            tool_name=tool.name,
            args=effective_args,
        )
        start = time.monotonic()
        result = await send_to_worker_fn(call)
        end = time.monotonic()
        duration_ms = int((end - start) * 1000)

        # If the worker returned an error, propagate as ToolOutput
        # with error text (the caller -- transport -- will see it).
        output_text = result.output
        output_data = result.data

        # Step 4 (brain): secret scrubbing (text AND structured data).
        if secret_registry is not None:
            output_text = scrub_text(secret_registry, output_text)
            output_data = scrub_data(secret_registry, output_data)

        # Step 5 (brain): mutation recording from worker's list.
        if mutation_tracker is not None and result.mutations:
            paths = mutation_tracker.setdefault(session_id, set())
            for path in result.mutations:
                paths.add(path)

        # Step 6 (brain): trace callback.
        if trace_callback is not None:
            await trace_callback(tool.name, args, output_text, duration_ms)

        # Step 7: return result.
        return ToolOutput(data=output_data, text=output_text)

    return Tool(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        execute=remote_execute,
        suspending=tool.suspending,
        namespace=tool.namespace,
        tags=tool.tags,
        deferred=tool.deferred,
        location=tool.location,
        capabilities=tool.capabilities,
    )


def wrap_tools_for_remote(
    tools: list[Tool],
    send_to_worker_fn: Callable[[ExecuteToolCall], Awaitable[ToolCallResult]],
    secret_registry: SecretRegistry | None,
    scheduler_check: Callable[[str], UUID],
    trace_callback: Callable[..., Any] | None,
    mutation_tracker: dict[str, set[str]] | None,
    session_id: str,
) -> list[Tool]:
    """Wrap all tools for remote execution on a worker."""
    return [
        wrap_tool_for_remote(
            tool=tool,
            send_to_worker_fn=send_to_worker_fn,
            secret_registry=secret_registry,
            scheduler_check=scheduler_check,
            trace_callback=trace_callback,
            mutation_tracker=mutation_tracker,
            session_id=session_id,
            is_start_task=(tool.name == "start_task"),
        )
        for tool in tools
    ]


def should_route_to_worker(
    tool_location: ToolLocation,
    execution_target: str | None,
) -> bool:
    """Decide whether a tool call should be sent to a worker.

    Returns True only when the task has an execution target set
    AND the tool's location allows remote execution (ANYWHERE).
    LOCAL tools always run on the brain regardless of target.
    """
    if execution_target is None:
        return False
    return tool_location == ToolLocation.ANYWHERE
