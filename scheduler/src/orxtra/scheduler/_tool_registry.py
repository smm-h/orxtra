"""Data-driven tool registry for building agent tool sets."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
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
    description: str = ""
    deferred: bool = False


@dataclass(frozen=True)
class Edge:
    """Advisory edge between two tools in the tool graph.

    Edges are purely advisory -- they inform suggestions but
    never enforce ordering or auto-loading.
    """

    source_tool: str
    target_tool: str
    edge_type: str  # e.g. "follows", "related_to"
    advisory: bool = True  # always True; field exists for explicitness


class ToolRegistry:
    """Registry of tool entries for data-driven tool construction.

    Built-in tools are registered at construction time. Custom tools
    can be added via ``register_custom``. The registry provides
    metadata for allow-list resolution and builds concrete Tool
    instances from a set of resolved names.
    """

    def __init__(self) -> None:
        self._entries: dict[str, ToolEntry] = {}
        self._edges: list[Edge] = []
        self._edges_from: dict[str, list[Edge]] = defaultdict(list)
        self._edges_to: dict[str, list[Edge]] = defaultdict(list)
        self._edges_by_type: dict[str, list[Edge]] = defaultdict(list)

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
        namespace: str,
        tags: frozenset[str],
        factory: Callable[[ToolDeps], Tool],
        description: str = "",
        deferred: bool = False,
    ) -> None:
        """Register a custom tool with full metadata.

        The factory receives ``ToolDeps`` like built-in factories.
        Namespace and tags are required -- no implicit defaults.
        """
        if name in self._entries:
            msg = f"Duplicate tool name: {name!r}"
            raise ValueError(msg)
        self._entries[name] = ToolEntry(
            name=name,
            namespace=namespace,
            tags=tags,
            factory=factory,
            description=description,
            deferred=deferred,
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

    def get_entry(self, name: str) -> ToolEntry | None:
        """Return the ToolEntry for a given name, or None."""
        return self._entries.get(name)

    def add_edge(
        self,
        source: str,
        target: str,
        edge_type: str,
    ) -> None:
        """Register an advisory edge between two tools.

        Both source and target must be registered tool names
        (or synthetic entries). Duplicate edges are silently
        ignored.
        """
        edge = Edge(
            source_tool=source,
            target_tool=target,
            edge_type=edge_type,
        )
        # Deduplicate: same (source, target, type) only once.
        if edge in self._edges:
            return
        self._edges.append(edge)
        self._edges_from[source].append(edge)
        self._edges_to[target].append(edge)
        self._edges_by_type[edge_type].append(edge)

    def edges_from(self, tool_name: str) -> list[Edge]:
        """Return all edges originating from a tool."""
        return list(self._edges_from.get(tool_name, []))

    def edges_to(self, tool_name: str) -> list[Edge]:
        """Return all edges pointing to a tool."""
        return list(self._edges_to.get(tool_name, []))

    def edges_by_type(self, edge_type: str) -> list[Edge]:
        """Return all edges of a given type."""
        return list(self._edges_by_type.get(edge_type, []))

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
        from orxtra.tool import make_read_tool
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
        description="Read a file's contents.",
    ))

    def _list_dir_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_list_dir_tool
        return make_list_dir_tool(deps.read_root)

    entries.append(ToolEntry(
        name="list_dir",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_list_dir_factory,
        description="List directory contents.",
    ))

    def _glob_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_glob_tool
        return make_glob_tool(deps.read_root)

    entries.append(ToolEntry(
        name="glob",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_glob_factory,
        description="Find files matching a glob pattern.",
    ))

    def _grep_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_grep_tool
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
        description="Search file contents with regex.",
    ))

    def _stat_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_stat_tool
        return make_stat_tool(deps.read_root)

    entries.append(ToolEntry(
        name="stat",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_stat_factory,
        description="Get file or directory metadata.",
    ))

    def _diff_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_diff_tool
        return make_diff_tool(deps.read_root)

    entries.append(ToolEntry(
        name="diff",
        namespace="fs.read",
        tags=frozenset({"readonly"}),
        factory=_diff_factory,
        description="Show differences between two files.",
    ))

    # -- Write tools (fs.write, mutation) --

    def _write_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_write_tool
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
        description="Write content to a file.",
    ))

    def _edit_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_edit_tool
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
        description="Apply targeted edits to a file.",
    ))

    def _multi_edit_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_multi_edit_tool
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
        description="Apply multiple targeted edits to a file.",
    ))

    def _mkdir_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_mkdir_tool
        return make_mkdir_tool(deps.read_root, deps.write_scope)

    entries.append(ToolEntry(
        name="mkdir",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_mkdir_factory,
        description="Create a directory.",
    ))

    def _move_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_move_tool
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
        description="Move or rename a file.",
    ))

    def _copy_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_copy_tool
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
        description="Copy a file.",
    ))

    def _delete_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_delete_tool
        return make_delete_tool(deps.read_root, deps.write_scope)

    entries.append(ToolEntry(
        name="delete",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_delete_factory,
        description="Delete a file or directory.",
    ))

    def _set_executable_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_set_executable_tool
        return make_set_executable_tool(
            deps.read_root, deps.write_scope,
        )

    entries.append(ToolEntry(
        name="set_executable",
        namespace="fs.write",
        tags=frozenset({"mutation"}),
        factory=_set_executable_factory,
        description="Set a file's executable permission.",
    ))

    # -- Notepad (io.notepad, mutation) --

    def _notepad_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_notepad_tool
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
        description="Append entries to the cross-agent notepad.",
    ))

    # -- HTTP (io.http, readonly+mutation) --

    def _http_factory(deps: ToolDeps) -> Tool:
        from orxtra.tool import make_http_tool
        _ = deps
        return make_http_tool(allowed_hosts="allow_all")

    entries.append(ToolEntry(
        name="http",
        namespace="io.http",
        tags=frozenset({"readonly", "mutation"}),
        factory=_http_factory,
        description="Make HTTP requests.",
    ))

    return entries


def _seed_builtin_edges(registry: ToolRegistry) -> None:
    """Seed the registry with advisory edges for obvious relationships.

    These are a small starter set. Edges are purely advisory --
    they drive result-appendix suggestions, never enforcement.
    """
    # "follows" edges: tool A's output is a natural input to tool B.
    _follows = [
        ("read", "edit"),
        ("read", "grep"),
        ("grep", "read"),
        ("glob", "read"),
        ("list_dir", "read"),
        ("diff", "edit"),
        ("stat", "read"),
    ]
    for source, target in _follows:
        registry.add_edge(source, target, "follows")

    # "related_to" edges: tools that are conceptually related.
    _related = [
        ("write", "read"),
        ("edit", "read"),
        ("move", "read"),
        ("copy", "read"),
    ]
    for source, target in _related:
        registry.add_edge(source, target, "related_to")


def create_builtin_registry() -> ToolRegistry:
    """Create a ToolRegistry populated with all built-in tools.

    Does NOT include: git (needs resolved_names context), consult
    (needs already-built tools), or lifecycle tools (always added
    unconditionally).

    Git and consult are handled separately in the build phase because
    they depend on the resolved tool set.
    """
    registry = ToolRegistry()
    for entry in _make_builtin_entries():
        registry.register(entry)
    _seed_builtin_edges(registry)
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


# ---------------------------------------------------------------------------
# Allow-list validation constants
# ---------------------------------------------------------------------------

# Lifecycle tools are always appended unconditionally; they are
# valid explicit allow-list entries even though they are not in
# the registry or synthetic entries.
LIFECYCLE_TOOL_NAMES = frozenset({
    "start_task",
    "end_task",
    "create_task",
    "create_workflow",
    "create_wait_for",
    "await_task",
})

# Metadata for tools that participate in allow-list resolution
# but are NOT registered as normal ToolEntry objects.  They are
# injected into the metadata dict before resolve_allow_list runs.
#
# MAINTENANCE CONTRACT:
#   - Phase 7.1: deferred declarations validated via
#     registry.get_entry (not synthetic entries).
SYNTHETIC_ENTRIES: dict[str, tuple[str, frozenset[str]]] = {
    "git": GIT_METADATA,
    "consult": CONSULT_METADATA,
}


def validate_allow_lists(
    agents: dict[str, Any],
    registry: ToolRegistry,
) -> None:
    """Validate every agent's allow list and deferred declarations.

    Called at Scheduler construction after all custom tools are
    registered, before any execution starts.

    Allow-list rules:
    - ``*`` (universal wildcard): always valid.
    - ``#tag``: the tag must exist in the known tag vocabulary
      (union of all tags across registry entries and synthetic
      entries).  Unknown tag = hard error.
    - ``ns.*`` (namespace wildcard): zero matches is fine --
      wildcards are the flexible mechanism for optional tool sets.
    - Explicit name: must exist in registry entries, synthetic
      entries, or lifecycle tool names.  Unknown = hard error.

    Deferred-list rules:
    - Every name in an agent's deferred list must exist in the
      registry (not synthetic entries or lifecycle tools --
      deferred tools must have a factory to build later).
      Unknown = hard error.
    - Deferred names must also be in the agent's allow list
      (deferred is a subset of allowed).

    Raises:
        ValueError: naming the agent and the offending entry.
    """
    # Build the complete metadata map.
    metadata = dict(registry.get_metadata())
    metadata.update(SYNTHETIC_ENTRIES)

    # Build known tag vocabulary from all metadata sources.
    known_tags: set[str] = set()
    for _, tags in metadata.values():
        known_tags.update(tags)

    # All names that are valid explicit allow-list entries.
    known_names = set(metadata.keys()) | LIFECYCLE_TOOL_NAMES

    # Inline tool names from all agents are also valid.
    for agent_def in agents.values():
        for itd in agent_def.inline_tools:
            known_names.add(itd.name)
            # Inline tools also contribute to the tag vocabulary.
            if itd.tags:
                known_tags.update(itd.tags)

    for agent_name, agent_def in agents.items():
        for entry in agent_def.allow:
            if entry == "*":
                continue
            if entry.startswith("#"):
                tag = entry[1:]
                if tag not in known_tags:
                    msg = (
                        f"Agent '{agent_name}' references "
                        f"unknown tag '{tag}' in allow list"
                    )
                    raise ValueError(msg)
            elif entry.endswith(".*"):
                # Namespace wildcard -- zero matches is fine.
                continue
            elif entry not in known_names:
                msg = (
                    f"Agent '{agent_name}' references "
                    f"unknown tool '{entry}' in allow list"
                )
                raise ValueError(msg)

        # Validate deferred declarations.
        for deferred_name in agent_def.deferred:
            # Deferred tools must exist in the registry
            # (must have a factory to build on demand).
            if registry.get_entry(deferred_name) is None:
                msg = (
                    f"Agent '{agent_name}' declares "
                    f"unknown deferred tool "
                    f"'{deferred_name}'"
                )
                raise ValueError(msg)
