"""Data-driven tool registry for building agent tool sets."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path
    from uuid import UUID

    from orxtra.agent import Agent
    from orxtra.protocols import Tool
    from orxtra.trace import StorageBackend, TraceWriter
    from orxtra.transport import Transport
    from orxtra.write_safety import StaleWriteTracker, WriteQueue


@dataclass
class ToolDeps:
    """Session-scoped dependencies available to tool factories."""

    read_root: Path
    write_scope: list[Path] | None
    write_queue: WriteQueue
    stale_tracker: StaleWriteTracker
    session_id: str
    trace_writer: TraceWriter | StorageBackend
    run_id: UUID
    task_id: UUID
    task_name: str
    task_agent: str
    scheduler_ref: Any  # TaskSchedulerRef protocol
    transport_registry: dict[str, Transport]
    categories: dict[str, str]
    agents: dict[str, Agent]
    preview_threshold: int
    preview_lines: int


@dataclass(frozen=True)
class ToolEntry:
    """Registry entry for a single tool."""

    name: str
    namespace: str
    tags: frozenset[str]
    factory: Callable[[ToolDeps], Tool]


class ToolRegistry:
    """Registry of tool entries for data-driven tool construction.

    Built-in tools are registered at construction time. Custom tools
    can be added via ``register_custom``. The registry provides
    metadata for allow-list resolution and builds concrete Tool
    instances from a set of resolved names.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}

    def register(self, entry: ToolEntry) -> None:
        """Register a tool entry.

        Raises ValueError on duplicate name.
        """
        if entry.name in self._entries:
            msg = f"Duplicate tool name: {entry.name!r}"
            raise ValueError(msg)
        self._entries[entry.name] = entry

    def register_custom(
        self,
        name: str,
        factory: Callable[..., Tool],
    ) -> None:
        """Register a custom tool (no-arg factory, no deps).

        Custom tools use empty namespace and tags since
        their metadata is not known to the registry.
        """
        if name in self._entries:
            msg = f"Duplicate tool name: {name!r}"
            raise ValueError(msg)

        def _wrap(deps: ToolDeps) -> Tool:
            _ = deps
            return factory()

        self._entries[name] = ToolEntry(
            name=name,
            namespace="",
            tags=frozenset(),
            factory=_wrap,
        )

    def get_metadata(
        self,
    ) -> dict[str, tuple[str, frozenset[str]]]:
        """Return name -> (namespace, tags) for all registered tools.

        Used by ``resolve_allow_list`` to match wildcards and tag filters.
        """
        return {
            name: (entry.namespace, entry.tags)
            for name, entry in self._entries.items()
        }

    def build_tools(
        self,
        names: set[str],
        deps: ToolDeps,
    ) -> list[Tool]:
        """Build Tool instances for the given names.

        Unknown names are silently skipped (the allow-list resolver
        may have included names not in the registry, e.g. custom tools
        that were not registered).
        """
        tools: list[Tool] = []
        for name in sorted(names):
            entry = self._entries.get(name)
            if entry is not None:
                tools.append(entry.factory(deps))
        return tools

    def __contains__(self, name: str) -> bool:
        return name in self._entries

    def __len__(self) -> int:
        return len(self._entries)


# ---------------------------------------------------------------------------
# Built-in tool registration
# ---------------------------------------------------------------------------

_WRITE_TOOL_NAMES = frozenset({
    "write", "edit", "multi_edit",
    "delete", "move", "copy",
    "mkdir", "set_executable",
})


def _make_builtin_entries() -> list[ToolEntry]:
    """Create ToolEntry objects for all 18 built-in tools.

    Import the make_* constructors lazily to avoid circular imports
    at module load time.
    """
    entries: list[ToolEntry] = []

    # -- Read tools (fs.read, readonly) --

    def _read_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_read_tool  # noqa: PLC0415
        return make_read_tool(
            deps.read_root,
            deps.preview_threshold,
            deps.preview_lines,
            session_id=deps.session_id,
        )

    entries.append(ToolEntry(
        name="read",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_read_factory,
    ))

    def _list_dir_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_list_dir_tool  # noqa: PLC0415
        return make_list_dir_tool(deps.read_root)

    entries.append(ToolEntry(
        name="list_dir",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_list_dir_factory,
    ))

    def _glob_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_glob_tool  # noqa: PLC0415
        return make_glob_tool(deps.read_root)

    entries.append(ToolEntry(
        name="glob",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_glob_factory,
    ))

    def _grep_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_grep_tool  # noqa: PLC0415
        return make_grep_tool(
            deps.read_root,
            deps.preview_threshold,
            deps.preview_lines,
        )

    entries.append(ToolEntry(
        name="grep",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_grep_factory,
    ))

    def _stat_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_stat_tool  # noqa: PLC0415
        return make_stat_tool(deps.read_root)

    entries.append(ToolEntry(
        name="stat",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_stat_factory,
    ))

    def _diff_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_diff_tool  # noqa: PLC0415
        return make_diff_tool(deps.read_root)

    entries.append(ToolEntry(
        name="diff",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_diff_factory,
    ))

    # -- Write tools (fs.write, mutation) --

    def _write_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_write_tool  # noqa: PLC0415
        return make_write_tool(
            deps.read_root, deps.write_scope,
            deps.write_queue, deps.stale_tracker,
            deps.session_id,
        )

    entries.append(ToolEntry(
        name="write",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_write_factory,
    ))

    def _edit_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_edit_tool  # noqa: PLC0415
        return make_edit_tool(
            deps.read_root, deps.write_scope,
            deps.write_queue, deps.stale_tracker,
            deps.session_id,
        )

    entries.append(ToolEntry(
        name="edit",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_edit_factory,
    ))

    def _multi_edit_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_multi_edit_tool  # noqa: PLC0415
        return make_multi_edit_tool(
            deps.read_root, deps.write_scope,
            deps.write_queue, deps.stale_tracker,
            deps.session_id,
        )

    entries.append(ToolEntry(
        name="multi_edit",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_multi_edit_factory,
    ))

    def _mkdir_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_mkdir_tool  # noqa: PLC0415
        return make_mkdir_tool(deps.read_root, deps.write_scope)

    entries.append(ToolEntry(
        name="mkdir",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_mkdir_factory,
    ))

    def _move_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_move_tool  # noqa: PLC0415
        return make_move_tool(
            deps.read_root, deps.write_scope,
            deps.write_queue, deps.stale_tracker,
            deps.session_id,
        )

    entries.append(ToolEntry(
        name="move",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_move_factory,
    ))

    def _copy_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_copy_tool  # noqa: PLC0415
        return make_copy_tool(
            deps.read_root, deps.write_scope,
            deps.write_queue, deps.stale_tracker,
            deps.session_id,
        )

    entries.append(ToolEntry(
        name="copy",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_copy_factory,
    ))

    def _delete_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_delete_tool  # noqa: PLC0415
        return make_delete_tool(deps.read_root, deps.write_scope)

    entries.append(ToolEntry(
        name="delete",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_delete_factory,
    ))

    def _set_executable_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_set_executable_tool  # noqa: PLC0415
        return make_set_executable_tool(
            deps.read_root, deps.write_scope,
        )

    entries.append(ToolEntry(
        name="set_executable",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_set_executable_factory,
    ))

    # -- Notepad (io.notepad, mutation) --

    def _notepad_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_notepad_tool  # noqa: PLC0415
        return make_notepad_tool(
            deps.trace_writer,
            str(deps.run_id),
            deps.task_name,
            deps.task_agent,
        )

    entries.append(ToolEntry(
        name="notepad",
        namespace="io.notepad",
        tags=frozenset({"mutation"}),
        factory=_notepad_factory,
    ))

    # -- HTTP (io.http, readonly+mutation) --

    def _http_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_http_tool  # noqa: PLC0415
        _ = deps
        return make_http_tool(allowed_hosts="allow_all")

    entries.append(ToolEntry(
        name="http",
        namespace="io.http",
        tags=frozenset({"readonly", "mutation"}),
        factory=_http_factory,
    ))

    return entries


def create_builtin_registry() -> ToolRegistry:
    """Create a ToolRegistry populated with all built-in tools.

    Does NOT include: git (needs resolved_names context), consult
    (needs already-built tools), exec/shell (per-agent config),
    or lifecycle tools (always added unconditionally).

    Git and consult are handled separately in the build phase because
    they depend on the resolved tool set.
    """
    registry = ToolRegistry()
    for entry in _make_builtin_entries():
        registry.register(entry)
    return registry


# Git metadata constant for allow-list resolution.
# Git is not registered as a normal entry because its factory
# needs to know which other tools are present (to decide
# whether 'commit' subcommand is available). But we still need
# its metadata for wildcard/tag resolution.
GIT_METADATA: tuple[str, frozenset[str]] = (
    "git", frozenset({"readonly", "mutation"}),
)

CONSULT_METADATA: tuple[str, frozenset[str]] = (
    "meta.consult", frozenset({"readonly"}),
)

# Names of tools that imply git commit access
WRITE_TOOL_NAMES = _WRITE_TOOL_NAMES
