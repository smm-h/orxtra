# Scheduler: custom tool injection mechanism

## Problem

`_build_agent_tools` only creates tools from orxt's built-in registry (read, write, edit, shell, exec, etc.). When an agent's `allow` list includes a name that doesn't match a built-in, it's silently ignored. There is no way to inject arbitrary `Tool` instances from a consumer project.

Consumer projects have domain-specific async Python tools (HTTP fetching with TLS fingerprinting, browser automation, API discovery, platform detection, rate limit probing) that can't be modeled as exec_tools (subprocess binaries) or shell commands. These are async functions that need closure-captured runtime context (database connections, browser handles, proxy configs).

## Solution

Add a `custom_tools` parameter to the Scheduler constructor: `dict[str, Callable[..., Tool]]` mapping tool names to factory callables. When `_build_agent_tools` processes an agent's `allow` list, after checking built-in tools, it checks `custom_tools` for any remaining names. Matching factories are called and the resulting Tool instances are appended to the agent's tool list.

## Affected files

| File | Change |
|------|--------|
| `scheduler/src/orxt/scheduler/_executor.py` | Add `custom_tools` param to `__init__`, store as `self._custom_tools`. Update `_build_agent_tools` to check custom_tools dict after built-in processing. |
| `scheduler/tests/test_executor.py` or new test file | Tests for custom tool injection. |

## Design details

**Factory signature:** Each factory in `custom_tools` is a `Callable[..., Tool]`. The scheduler calls it with no arguments — all context must be captured by the factory's closure at construction time. This matches the pattern used by built-in factories (e.g., `make_read_tool(read_root, ...)` closes over `read_root`).

**Allow list interaction:** An agent's `[tools] allow` list can freely mix built-in names and custom names. Built-ins are resolved first. Remaining names are checked against `custom_tools`. Names in neither are silently ignored (same behavior as today for unknown names).

**Name collision:** If a custom tool name matches a built-in name, the built-in wins. Custom tools cannot override built-ins. This prevents consumers from accidentally shadowing core functionality.

**Pipeline wrapping:** Custom tools are wrapped by `wrap_tools_for_session` just like built-ins — they get secret substitution, tracing, mutation tracking, and the compose mechanism for free.

**Lifecycle tools:** The always-added lifecycle tools (start_task, end_task, create_task, await_task, etc.) remain unconditional and cannot be overridden by custom tools.

## Effort estimate

Small. ~20 lines in the executor (constructor param + loop in `_build_agent_tools`). One test file with 3-4 tests: custom tool appears in session, custom tool works in orchestrator mode, name collision with built-in resolves to built-in, unknown name in allow list is ignored.
