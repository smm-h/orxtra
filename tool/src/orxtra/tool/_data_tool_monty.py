"""Factory for building Tool instances from MontyExecution definitions.

Takes a DataToolDefinition with a MontyExecution config and builds a
concrete Tool whose execute function:

1. Validates agent-supplied args against the param schema.
2. Compiles the monty code (once at factory time for efficiency).
3. Builds capability host functions that invoke built-in tools
   THROUGH THE PIPELINE (write queue, path scopes, safegit/saferm,
   scrubbing, tracing).
4. Runs the script via ``monty.run_async`` with resource limits.
5. Validates output against the definition's output schema (if any).
6. Returns a ToolOutput with the validated data.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from pydantic_monty import Monty, MontyRuntimeError

from orxtra.protocols import Tool, ToolError, ToolOutput
from orxtra.tool._data_tool_shared import (
    build_json_schema_params,
    validate_args,
    validate_output_schema,
)
from orxtra.tool._data_tool_types import (
    CommandExecution,
    DataToolDefinition,
    MontyExecution,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine

    from orxtra.scheduler._tool_registry import ToolDeps

# ---------------------------------------------------------------------------
# Capability mapping
# ---------------------------------------------------------------------------

# Maps capability names to the built-in tool tags they imply.
# A capability is "mutation" if it can modify state; "readonly" otherwise.
_MUTATION_CAPABILITIES: frozenset[str] = frozenset({
    "write", "edit", "command", "delete", "move", "copy",
    "mkdir", "set_executable", "multi_edit",
})

_READONLY_CAPABILITIES: frozenset[str] = frozenset({
    "read", "list_dir", "glob", "grep", "stat", "diff",
})


def _derive_tags(
    capabilities: list[str],
    user_tags: list[str] | None,
) -> frozenset[str]:
    """Derive effect tags from granted capabilities.

    If any capability is a mutation capability, the tool gets the
    "mutation" tag. If all are readonly, it gets "readonly".
    """
    tags: set[str] = set()
    if user_tags:
        tags.update(user_tags)

    has_mutation = any(cap in _MUTATION_CAPABILITIES for cap in capabilities)
    if has_mutation:
        tags.add("mutation")
    else:
        tags.add("readonly")

    return frozenset(tags)


# ---------------------------------------------------------------------------
# Capability host function builders
# ---------------------------------------------------------------------------


def _build_read_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``read`` capability: reads a file and returns its content."""
    from orxtra.tool import make_read_tool  # noqa: PLC0415

    tool = make_read_tool(
        deps.read_root,
        deps.preview_threshold,
        deps.preview_lines,
        session_id=deps.session_id,
    )

    async def read_file(path: str) -> str:
        result = await tool.execute({"path": path})
        return result.text

    return read_file


def _build_write_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``write`` capability: writes a file through write-safety."""
    from orxtra.tool import make_write_tool  # noqa: PLC0415

    tool = make_write_tool(
        deps.read_root,
        deps.write_scope,
        deps.write_queue,
        deps.stale_tracker,
        deps.session_id,
    )

    async def write_file(path: str, content: str) -> str:
        result = await tool.execute({"path": path, "content": content})
        return result.text

    return write_file


def _build_edit_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``edit`` capability: find-and-replace through write-safety."""
    from orxtra.tool import make_edit_tool  # noqa: PLC0415

    tool = make_edit_tool(
        deps.read_root,
        deps.write_scope,
        deps.write_queue,
        deps.stale_tracker,
        deps.session_id,
    )

    async def edit_file(
        path: str,
        old_string: str,
        new_string: str,
    ) -> str:
        result = await tool.execute({
            "path": path,
            "old_string": old_string,
            "new_string": new_string,
        })
        return result.text

    return edit_file


def _build_http_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``http`` capability: makes HTTP requests."""
    from orxtra.tool import make_http_tool  # noqa: PLC0415

    tool = make_http_tool(allowed_hosts="allow_all")
    _ = deps  # http tool doesn't need deps

    async def http_request(
        method: str,
        url: str,
        headers: dict[str, str] | None = None,
        body: str | None = None,
    ) -> str:
        args: dict[str, Any] = {"method": method, "url": url}
        if headers is not None:
            args["headers"] = headers
        if body is not None:
            args["body"] = body
        result = await tool.execute(args)
        return result.text

    return http_request


def _build_command_capability(
    definition: DataToolDefinition,
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``command`` capability: subprocess execution.

    Uses the definition's CommandExecution config for the pinned
    executable and arg validation settings, backed by the relocated
    subprocess machinery.
    """
    from orxtra.tool._subprocess import run_subprocess  # noqa: PLC0415

    # The command capability config is in the MontyExecution's
    # parent definition. We need a CommandExecution config to be
    # passed separately. For monty tools, the command capability
    # config is embedded in the definition itself. We look for a
    # command_config attribute or use defaults.
    exec_cfg = definition.execution
    if not isinstance(exec_cfg, MontyExecution):
        msg = "Expected MontyExecution config"
        raise TypeError(msg)

    # Command capability gets its config from the definition's
    # command_config if provided, otherwise from sensible defaults.
    # In the full system, command config will be part of the
    # capability grant in the definition.
    command_config = getattr(definition, "_command_config", None)

    async def run_command(
        executable: str,
        args: list[str] | None = None,
        timeout: int | None = None,
    ) -> str:
        cmd_args = args if args is not None else []

        # Use command config if available, else definition limits.
        timeout_ceiling = (
            command_config.timeout_ceiling
            if command_config is not None
            else exec_cfg.limits.max_duration_secs
        )
        arg_val = (
            command_config.arg_validation
            if command_config is not None
            else True
        )

        effective_timeout = min(
            timeout if timeout is not None else timeout_ceiling,
            timeout_ceiling,
        )

        result = await run_subprocess(
            executable=executable,
            args=cmd_args,
            cwd=deps.read_root,
            timeout=effective_timeout,
            arg_validation=arg_val,
            preview_threshold=deps.preview_threshold,
            preview_lines=deps.preview_lines,
        )
        return result.text

    return run_command


def _build_list_dir_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``list_dir`` capability."""
    from orxtra.tool import make_list_dir_tool  # noqa: PLC0415

    tool = make_list_dir_tool(deps.read_root)

    async def list_dir(path: str) -> str:
        result = await tool.execute({"path": path})
        return result.text

    return list_dir


def _build_grep_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``grep`` capability."""
    from orxtra.tool import make_grep_tool  # noqa: PLC0415

    tool = make_grep_tool(
        deps.read_root,
        deps.preview_threshold,
        deps.preview_lines,
    )

    async def grep(pattern: str, path: str | None = None) -> str:
        args: dict[str, Any] = {"pattern": pattern}
        if path is not None:
            args["path"] = path
        result = await tool.execute(args)
        return result.text

    return grep


def _build_glob_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``glob`` capability."""
    from orxtra.tool import make_glob_tool  # noqa: PLC0415

    tool = make_glob_tool(deps.read_root)

    async def glob_files(pattern: str, path: str | None = None) -> str:
        args: dict[str, Any] = {"pattern": pattern}
        if path is not None:
            args["path"] = path
        result = await tool.execute(args)
        return result.text

    return glob_files


def _build_stat_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``stat`` capability."""
    from orxtra.tool import make_stat_tool  # noqa: PLC0415

    tool = make_stat_tool(deps.read_root)

    async def stat_file(path: str) -> str:
        result = await tool.execute({"path": path})
        return result.text

    return stat_file


def _build_diff_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``diff`` capability."""
    from orxtra.tool import make_diff_tool  # noqa: PLC0415

    tool = make_diff_tool(deps.read_root)

    async def diff_files(path_a: str, path_b: str) -> str:
        result = await tool.execute({"path_a": path_a, "path_b": path_b})
        return result.text

    return diff_files


def _build_delete_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``delete`` capability."""
    from orxtra.tool import make_delete_tool  # noqa: PLC0415

    tool = make_delete_tool(deps.read_root, deps.write_scope)

    async def delete_file(
        path: str,
        description: str,
        recursive: bool = False,
    ) -> str:
        result = await tool.execute({
            "path": path,
            "description": description,
            "recursive": recursive,
        })
        return result.text

    return delete_file


def _build_move_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``move`` capability."""
    from orxtra.tool import make_move_tool  # noqa: PLC0415

    tool = make_move_tool(
        deps.read_root, deps.write_scope,
        deps.write_queue, deps.stale_tracker,
        deps.session_id,
    )

    async def move_file(source: str, destination: str) -> str:
        result = await tool.execute({
            "source": source,
            "destination": destination,
        })
        return result.text

    return move_file


def _build_copy_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``copy`` capability."""
    from orxtra.tool import make_copy_tool  # noqa: PLC0415

    tool = make_copy_tool(
        deps.read_root, deps.write_scope,
        deps.write_queue, deps.stale_tracker,
        deps.session_id,
    )

    async def copy_file(source: str, destination: str) -> str:
        result = await tool.execute({
            "source": source,
            "destination": destination,
        })
        return result.text

    return copy_file


def _build_mkdir_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``mkdir`` capability."""
    from orxtra.tool import make_mkdir_tool  # noqa: PLC0415

    tool = make_mkdir_tool(deps.read_root, deps.write_scope)

    async def make_directory(path: str) -> str:
        result = await tool.execute({"path": path})
        return result.text

    return make_directory


def _build_set_executable_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``set_executable`` capability."""
    from orxtra.tool import make_set_executable_tool  # noqa: PLC0415

    tool = make_set_executable_tool(deps.read_root, deps.write_scope)

    async def set_executable(path: str) -> str:
        result = await tool.execute({"path": path})
        return result.text

    return set_executable


def _build_multi_edit_capability(
    deps: ToolDeps,
) -> Callable[..., Coroutine[Any, Any, Any]]:
    """Build the ``multi_edit`` capability."""
    from orxtra.tool import make_multi_edit_tool  # noqa: PLC0415

    tool = make_multi_edit_tool(
        deps.read_root, deps.write_scope,
        deps.write_queue, deps.stale_tracker,
        deps.session_id,
    )

    async def multi_edit(edits: list[dict[str, str]]) -> str:
        result = await tool.execute({"edits": edits})
        return result.text

    return multi_edit


# Registry mapping capability names to their builder functions.
# The command capability is special (needs definition context) and
# is handled separately.
_CAPABILITY_BUILDERS: dict[
    str,
    Callable[[ToolDeps], Callable[..., Coroutine[Any, Any, Any]]],
] = {
    "read": _build_read_capability,
    "write": _build_write_capability,
    "edit": _build_edit_capability,
    "http": _build_http_capability,
    "list_dir": _build_list_dir_capability,
    "grep": _build_grep_capability,
    "glob": _build_glob_capability,
    "stat": _build_stat_capability,
    "diff": _build_diff_capability,
    "delete": _build_delete_capability,
    "move": _build_move_capability,
    "copy": _build_copy_capability,
    "mkdir": _build_mkdir_capability,
    "set_executable": _build_set_executable_capability,
    "multi_edit": _build_multi_edit_capability,
}


def build_capability_functions(
    capabilities: list[str],
    definition: DataToolDefinition,
    deps: ToolDeps,
) -> dict[str, Callable[..., Coroutine[Any, Any, Any]]]:
    """Build the external function dict for monty from granted capabilities.

    Each capability name maps to an async host function that invokes
    the corresponding built-in tool. Unrecognized capability names
    raise a hard error at construction time.
    """
    functions: dict[str, Callable[..., Coroutine[Any, Any, Any]]] = {}

    for cap_name in capabilities:
        if cap_name == "command":
            functions[cap_name] = _build_command_capability(
                definition, deps,
            )
        elif cap_name in _CAPABILITY_BUILDERS:
            functions[cap_name] = _CAPABILITY_BUILDERS[cap_name](deps)
        else:
            msg = (
                f"Unknown capability {cap_name!r} in monty tool "
                f"{definition.name!r}"
            )
            raise ValueError(msg)

    return functions


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def build_monty_tool(
    definition: DataToolDefinition,
    deps: ToolDeps,
) -> Tool:
    """Build a Tool from a DataToolDefinition with MontyExecution config.

    Args:
        definition: A validated DataToolDefinition with ``type = "monty"``.
        deps: Session-scoped dependencies for capability construction.

    Returns:
        A Tool instance ready for execution pipeline wrapping.
    """
    exec_cfg = definition.execution
    if not isinstance(exec_cfg, MontyExecution):
        msg = (
            f"Expected MontyExecution config, "
            f"got {type(exec_cfg).__name__}"
        )
        raise TypeError(msg)

    # Compile the monty code once at factory time.
    # Declare input variable names from the param definitions so
    # monty knows what globals to inject at run time.
    input_names = list(definition.params.keys())
    try:
        monty = Monty(
            exec_cfg.code,
            inputs=input_names if input_names else None,
        )
    except Exception as exc:
        msg = (
            f"Failed to compile monty code for tool "
            f"{definition.name!r}: {exc}"
        )
        raise ToolError(msg) from exc

    # Build capability host functions.
    capability_functions = build_capability_functions(
        exec_cfg.capabilities, definition, deps,
    )

    # Build LLM-visible parameter schema.
    params = dict(definition.params)
    parameters = build_json_schema_params(params)
    output_schema = (
        definition.output.schema_ if definition.output else None
    )

    # Derive effect tags from capabilities.
    tags = _derive_tags(exec_cfg.capabilities, definition.tags)

    # Build resource limits for monty.
    monty_limits: dict[str, Any] = {
        "max_duration_secs": float(exec_cfg.limits.max_duration_secs),
    }
    if exec_cfg.limits.max_allocations is not None:
        monty_limits["max_allocations"] = exec_cfg.limits.max_allocations
    if exec_cfg.limits.max_memory is not None:
        monty_limits["max_memory"] = exec_cfg.limits.max_memory

    async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
        validate_args(args, params)

        try:
            result = await monty.run_async(
                inputs=args if args else None,
                external_functions=capability_functions,
                limits=monty_limits,
            )
        except MontyRuntimeError as exc:
            # Check if the inner exception is a ToolError from a capability.
            inner = exc.exception()
            if isinstance(inner, ToolError):
                raise inner from exc
            msg = (
                f"Monty script error in tool {definition.name!r}: "
                f"{exc}"
            )
            raise ToolError(msg) from exc

        # Validate output schema if defined.
        if output_schema is not None:
            validate_output_schema(result, output_schema)

        return ToolOutput(
            data=result,
            text=json.dumps(result) if not isinstance(result, str) else result,
        )

    return Tool(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
        execute=execute,
        namespace=definition.namespace,
        tags=tags,
        deferred=definition.deferred,
    )


def build_command_tool(
    definition: DataToolDefinition,
    deps: ToolDeps,
) -> Tool:
    """Build a Tool from a DataToolDefinition with CommandExecution config.

    The command execution type is a thin wrapper around the relocated
    subprocess machinery. The definition specifies the pinned
    executable, arg validation, and timeout ceiling.

    Args:
        definition: A validated DataToolDefinition with ``type = "command"``.
        deps: Session-scoped dependencies.

    Returns:
        A Tool instance ready for execution pipeline wrapping.
    """
    exec_cfg = definition.execution
    if not isinstance(exec_cfg, CommandExecution):
        msg = (
            f"Expected CommandExecution config, "
            f"got {type(exec_cfg).__name__}"
        )
        raise TypeError(msg)

    from orxtra.tool._subprocess import run_subprocess  # noqa: PLC0415

    params = dict(definition.params)
    parameters = build_json_schema_params(params)
    output_schema = (
        definition.output.schema_ if definition.output else None
    )

    # Command tools are always mutation.
    user_tags = list(definition.tags) if definition.tags else []
    user_tags.append("mutation")
    tags = frozenset(user_tags)

    executable = exec_cfg.executable
    arg_validation = exec_cfg.arg_validation
    timeout_ceiling = exec_cfg.timeout_ceiling

    async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
        validate_args(args, params)

        cmd_args: list[str] = args.get("args", [])
        timeout: int | None = args.get("timeout")
        effective_timeout = min(
            timeout if timeout is not None else timeout_ceiling,
            timeout_ceiling,
        )

        result = await run_subprocess(
            executable=executable,
            args=cmd_args,
            cwd=deps.read_root,
            timeout=effective_timeout,
            arg_validation=arg_validation,
            preview_threshold=deps.preview_threshold,
            preview_lines=deps.preview_lines,
        )

        if output_schema is not None:
            validate_output_schema(result.data, output_schema)

        return result

    return Tool(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
        execute=execute,
        namespace=definition.namespace,
        tags=tags,
        deferred=definition.deferred,
    )
