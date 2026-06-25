from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING, Any, cast

from orxtra.protocols._results import ToolOutput
from orxtra.protocols._tool import Tool
from orxtra.write_safety import with_transient_retry

if TYPE_CHECKING:
    from collections.abc import Callable
    from uuid import UUID

    from orxtra.secrets import SecretRegistry

FILE_MUTATION_TOOLS: frozenset[str] = frozenset({
    "write", "edit", "multi_edit", "delete", "move", "copy", "mkdir",
    "set_executable", "shell",
})


def wrap_tool_with_pipeline(  # noqa: C901, PLR0913
    tool: Tool,
    scheduler_check: Callable[[str], UUID],
    secret_registry: SecretRegistry | None,
    trace_callback: Callable[..., Any] | None,
    session_id: str,
    is_start_task: bool = False,
    is_file_mutation: bool = False,
    mutation_tracker: dict[str, set[str]] | None = None,
) -> Tool:
    """Return a new Tool with the same schema but a wrapped execute that runs
    the full pipeline: active-task check, secret substitution, execution,
    secret scrubbing, mutation tracking, and trace callback."""

    async def wrapped_execute(args: dict[str, Any]) -> ToolOutput[Any]:
        # 1. Active task check (skip for start_task itself).
        if not is_start_task:
            scheduler_check(session_id)

        # 2. Secret substitution.
        if secret_registry is not None:
            serialized = json.dumps(args)
            substituted = secret_registry.substitute(serialized)
            effective_args: dict[str, Any] = json.loads(substituted)
        else:
            effective_args = args

        # 3. Execute (timed, with transient retry).
        start = time.monotonic()
        result = await with_transient_retry(tool.execute, effective_args)
        end = time.monotonic()
        duration_ms = int((end - start) * 1000)

        # 4. Secret scrubbing.
        if secret_registry is not None:
            result = ToolOutput(data=result.data, text=secret_registry.scrub(result.text))

        # 5. Mutation tracking.
        if is_file_mutation and mutation_tracker is not None:
            paths = mutation_tracker.setdefault(session_id, set())
            # Extract path from tool args
            if "path" in effective_args:
                paths.add(str(effective_args["path"]))
            if "source" in effective_args:
                paths.add(str(effective_args["source"]))
            if "destination" in effective_args:
                paths.add(str(effective_args["destination"]))
            # shell tool has "command" but no path -- track as generic mutation
            if not paths:
                paths.add("__generic__")

        # 6. Trace callback (trace expects str).
        if trace_callback is not None:
            await trace_callback(tool.name, args, result.text, duration_ms)

        # 7. Return result.
        return result

    wrapped_execute._raw_execute = getattr(tool.execute, "_raw_execute", tool.execute)  # type: ignore[attr-defined]  # noqa: SLF001  # accessing orxtra internal API

    return Tool(
        name=tool.name,
        description=tool.description,
        parameters=tool.parameters,
        execute=wrapped_execute,
        suspending=tool.suspending,
        namespace=tool.namespace,
        tags=tool.tags,
        deferred=tool.deferred,
    )


def wrap_tools_for_session(  # noqa: PLR0913
    tools: list[Tool],
    scheduler_check: Callable[[str], UUID],
    secret_registry: SecretRegistry | None,
    trace_callback: Callable[..., Any] | None,
    session_id: str,
    mutation_tracker: dict[str, set[str]] | None = None,
) -> list[Tool]:
    """Wrap all tools in the list with the execution pipeline."""
    return [
        wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=scheduler_check,
            secret_registry=secret_registry,
            trace_callback=trace_callback,
            session_id=session_id,
            is_start_task=(tool.name == "start_task"),
            is_file_mutation=(tool.name in FILE_MUTATION_TOOLS),
            mutation_tracker=mutation_tracker,
        )
        for tool in tools
    ]


async def compose(tool: Tool, args: dict[str, Any]) -> ToolOutput[Any]:
    """Call a tool's raw execute, bypassing the pipeline.

    Use when tool A calls tool B as an implementation detail.
    B's execution is not traced, not scrubbed, not mutation-tracked.
    Attribution goes to the outer tool A.
    """
    raw = getattr(tool.execute, "_raw_execute", None)
    if raw is not None:
        return cast("ToolOutput[Any]", await raw(args))
    return await tool.execute(args)
