"""Tests for run-start allow-list validation.

Covers:
- register_custom with real namespace/tags/deps-aware factory
- validate_allow_lists: typo'd explicit names, typo'd tags, lifecycle
  tool names as valid entries, namespace wildcards matching zero,
  known tags matching zero tools, synthetic entries resolution.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
import uuid6
from orxtra.agent import Agent
from orxtra.protocols import Tool
from orxtra.scheduler._tool_registry import (
    LIFECYCLE_TOOL_NAMES,
    SYNTHETIC_ENTRIES,
    ToolDeps,
    ToolEntry,
    ToolRegistry,
    create_builtin_registry,
    validate_allow_lists,
)
from orxtra.write_safety import StaleWriteTracker, WriteQueue


def _make_dummy_tool(name: str) -> Tool:
    return Tool(
        name=name,
        description=f"Test tool: {name}",
        parameters={"type": "object", "properties": {}},
        execute=AsyncMock(return_value="ok"),
    )


def _make_deps(tmp_path: Path) -> ToolDeps:
    return ToolDeps(
        read_root=tmp_path,
        write_scope=None,
        write_queue=WriteQueue(),
        stale_tracker=StaleWriteTracker(),
        session_id="test-session",
        trace_writer=MagicMock(),
        run_id=uuid6.uuid7(),
        task_id=uuid6.uuid7(),
        task_name="test-task",
        task_agent="test-agent",
        scheduler_ref=MagicMock(),
        transport_registry={},
        categories={},
        agents={},
        preview_threshold=10000,
        preview_lines=50,
    )


def _agent(
    name: str,
    allow: list[str],
) -> Agent:
    return Agent(
        name=name,
        description="Test agent",
        prompt="You are a test agent.",
        category="default",
        allow=allow,
    )


# ---------------------------------------------------------------
# register_custom with real metadata
# ---------------------------------------------------------------


class TestRegisterCustomMetadata:
    """Custom tools carry real namespace and tags."""

    def test_metadata_returned(self) -> None:
        registry = ToolRegistry()
        registry.register_custom(
            "my_tool",
            namespace="custom.analytics",
            tags=frozenset({"readonly", "external"}),
            factory=lambda deps: _make_dummy_tool("my_tool"),
        )
        meta = registry.get_metadata()
        assert meta["my_tool"] == (
            "custom.analytics",
            frozenset({"readonly", "external"}),
        )

    def test_namespace_wildcard_matches_custom(
        self,
    ) -> None:
        """Custom tool with namespace 'custom.analytics'
        should be matched by 'custom.*' wildcard."""
        from orxtra.scheduler._allow_resolver import (
            resolve_allow_list,
        )

        registry = ToolRegistry()
        registry.register_custom(
            "my_tool",
            namespace="custom.analytics",
            tags=frozenset({"readonly"}),
            factory=lambda deps: _make_dummy_tool("my_tool"),
        )
        result = resolve_allow_list(
            ["custom.*"], registry.get_metadata(),
        )
        assert "my_tool" in result

    def test_tag_filter_matches_custom(self) -> None:
        """Custom tool tagged 'external' should be matched
        by '#external' tag filter."""
        from orxtra.scheduler._allow_resolver import (
            resolve_allow_list,
        )

        registry = ToolRegistry()
        registry.register_custom(
            "my_tool",
            namespace="custom.analytics",
            tags=frozenset({"external"}),
            factory=lambda deps: _make_dummy_tool("my_tool"),
        )
        result = resolve_allow_list(
            ["#external"], registry.get_metadata(),
        )
        assert "my_tool" in result

    def test_deps_passed_to_factory(
        self, tmp_path: Path,
    ) -> None:
        """The deps-aware factory receives ToolDeps when
        the tool is built."""
        received_deps: list[ToolDeps] = []

        def capturing_factory(deps: ToolDeps) -> Tool:
            received_deps.append(deps)
            return _make_dummy_tool("my_tool")

        registry = ToolRegistry()
        registry.register_custom(
            "my_tool",
            namespace="custom.test",
            tags=frozenset(),
            factory=capturing_factory,
        )
        deps = _make_deps(tmp_path)
        registry.build_tools({"my_tool"}, deps)

        assert len(received_deps) == 1
        assert received_deps[0] is deps


# ---------------------------------------------------------------
# validate_allow_lists: explicit names
# ---------------------------------------------------------------


class TestValidateExplicitNames:
    """Unknown explicit allow entries raise ValueError."""

    def test_typo_explicit_name_raises(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "coder": _agent("coder", ["raed"]),
        }
        with pytest.raises(
            ValueError,
            match=r"Agent 'coder' references unknown tool 'raed'",
        ):
            validate_allow_lists(agents, registry)

    def test_known_builtin_name_passes(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "reader": _agent("reader", ["read", "grep"]),
        }
        # Should not raise.
        validate_allow_lists(agents, registry)

    def test_multiple_agents_one_bad(self) -> None:
        """Validation stops at the first invalid agent."""
        registry = create_builtin_registry()
        agents = {
            "good": _agent("good", ["read"]),
            "bad": _agent("bad", ["nonexistent_tool"]),
        }
        with pytest.raises(
            ValueError,
            match=r"Agent 'bad' references unknown tool 'nonexistent_tool'",
        ):
            validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# validate_allow_lists -- tags
# ---------------------------------------------------------------


class TestValidateTags:
    """Unknown tags raise ValueError; known tags pass."""

    def test_typo_tag_raises(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "coder": _agent("coder", ["#mutatin"]),
        }
        with pytest.raises(
            ValueError,
            match=r"Agent 'coder' references unknown tag 'mutatin'",
        ):
            validate_allow_lists(agents, registry)

    def test_known_tag_passes(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "coder": _agent("coder", ["#readonly"]),
        }
        validate_allow_lists(agents, registry)

    def test_known_tag_zero_matches_passes(self) -> None:
        """A tag that exists in the vocabulary but matches
        zero registered tools should pass.  This can happen
        when custom tools with unique tags are registered but
        the agent's allow list uses a tag from the synthetic
        entries."""
        # "mutation" is a known tag (from builtins and
        # synthetics).  Create a registry with NO mutation
        # tools but with the tag still in the vocabulary
        # via a custom entry.
        registry = ToolRegistry()
        registry.register_custom(
            "only_tool",
            namespace="test",
            tags=frozenset({"special_tag"}),
            factory=lambda deps: _make_dummy_tool("only_tool"),
        )
        # "mutation" exists in SYNTHETIC_ENTRIES tags, so
        # it IS known even if no registered tool has it.
        agents = {
            "a": _agent("a", ["#mutation"]),
        }
        validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# validate_allow_lists -- wildcards
# ---------------------------------------------------------------


class TestValidateWildcards:
    """Namespace wildcards matching zero are fine."""

    def test_wildcard_zero_matches_passes(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "a": _agent("a", ["custom.*"]),
        }
        # No custom tools registered, but wildcard is fine.
        validate_allow_lists(agents, registry)

    def test_universal_wildcard_passes(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "a": _agent("a", ["*"]),
        }
        validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# validate_allow_lists: lifecycle tools
# ---------------------------------------------------------------


class TestValidateLifecycleTools:
    """Lifecycle tool names in the allow list pass."""

    @pytest.mark.parametrize(
        "tool_name",
        sorted(LIFECYCLE_TOOL_NAMES),
    )
    def test_lifecycle_name_passes(
        self, tool_name: str,
    ) -> None:
        registry = create_builtin_registry()
        agents = {
            "a": _agent("a", [tool_name]),
        }
        # Should not raise -- lifecycle tools are valid
        # explicit allow entries.
        validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# validate_allow_lists: synthetic entries
# ---------------------------------------------------------------


class TestValidateSyntheticEntries:
    """All four synthetic entries resolve correctly."""

    @pytest.mark.parametrize(
        "entry_name",
        sorted(SYNTHETIC_ENTRIES.keys()),
    )
    def test_synthetic_entry_passes(
        self, entry_name: str,
    ) -> None:
        registry = create_builtin_registry()
        agents = {
            "a": _agent("a", [entry_name]),
        }
        validate_allow_lists(agents, registry)

    def test_synthetic_tags_are_known(self) -> None:
        """Tags from synthetic entries should be recognized
        as valid in tag filters."""
        registry = create_builtin_registry()
        # All synthetic tags should be known.
        for (_ns, tags) in SYNTHETIC_ENTRIES.values():
            for tag in tags:
                agents = {
                    "a": _agent("a", [f"#{tag}"]),
                }
                # Should not raise.
                validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# validate_allow_lists: empty allow list
# ---------------------------------------------------------------


class TestValidateEmptyAllow:
    """Empty allow list passes validation (nothing to check)."""

    def test_empty_allow_passes(self) -> None:
        registry = create_builtin_registry()
        agents = {
            "a": _agent("a", []),
        }
        validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# validate_allow_lists: deferred declarations
# ---------------------------------------------------------------


class TestValidateDeferred:
    """Deferred declarations validate against registry."""

    def test_known_deferred_passes(self) -> None:
        """A deferred tool that exists in the registry passes."""
        registry = create_builtin_registry()
        agent = Agent(
            name="coder",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["read", "grep"],
            deferred=["grep"],
        )
        agents = {"coder": agent}
        # Should not raise.
        validate_allow_lists(agents, registry)

    def test_unknown_deferred_raises(self) -> None:
        """A deferred tool not in the registry is a hard error."""
        registry = create_builtin_registry()
        agent = Agent(
            name="coder",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["read"],
            deferred=["nonexistent_tool"],
        )
        agents = {"coder": agent}
        with pytest.raises(
            ValueError,
            match=(
                r"Agent 'coder' declares unknown "
                r"deferred tool 'nonexistent_tool'"
            ),
        ):
            validate_allow_lists(agents, registry)

    def test_deferred_synthetic_raises(self) -> None:
        """Synthetic entries (git, consult) cannot be deferred
        because they have no factory in the registry."""
        registry = create_builtin_registry()
        agent = Agent(
            name="coder",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["git"],
            deferred=["git"],
        )
        agents = {"coder": agent}
        with pytest.raises(
            ValueError,
            match=r"Agent 'coder' declares unknown deferred tool 'git'",
        ):
            validate_allow_lists(agents, registry)

    def test_deferred_custom_passes(self) -> None:
        """Custom tools registered with full metadata can
        be deferred."""
        registry = create_builtin_registry()
        registry.register_custom(
            "my_custom",
            namespace="custom.test",
            tags=frozenset({"readonly"}),
            factory=lambda deps: _make_dummy_tool("my_custom"),
            description="My custom tool.",
        )
        agent = Agent(
            name="coder",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["my_custom"],
            deferred=["my_custom"],
        )
        agents = {"coder": agent}
        # Should not raise.
        validate_allow_lists(agents, registry)

    def test_empty_deferred_passes(self) -> None:
        """An agent with no deferred declarations passes."""
        registry = create_builtin_registry()
        agent = Agent(
            name="coder",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["read"],
            deferred=[],
        )
        agents = {"coder": agent}
        validate_allow_lists(agents, registry)


# ---------------------------------------------------------------
# Scheduler construction integration
# ---------------------------------------------------------------


class TestSchedulerConstructionValidation:
    """Validation is called during Scheduler.__init__."""

    def test_bad_allow_list_fails_at_construction(
        self, tmp_path: Path,
    ) -> None:
        """A typo'd allow entry causes Scheduler
        construction to fail with ValueError."""
        from orxtra.scheduler._executor import Scheduler

        from .conftest import (
            TEST_RUN_PRINCIPAL_ID,
            MockTraceWriter,
            MockTransport,
            make_categories,
        )

        agent = _agent("coder", ["raed"])
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)

        with pytest.raises(
            ValueError,
            match=r"Agent 'coder' references unknown tool 'raed'",
        ):
            Scheduler(
                run_principal_id=TEST_RUN_PRINCIPAL_ID,
                trace_writer=trace,  # type: ignore[arg-type]
                transport_registry={"anthropic": transport},  # type: ignore[dict-item]
                agents={"coder": agent},
                categories=make_categories(),
                run_id=uuid6.uuid7(),
                read_root=tmp_path,
                autonomy_level="max",
            )

    def test_bad_tag_fails_at_construction(
        self, tmp_path: Path,
    ) -> None:
        """A typo'd tag causes Scheduler construction
        to fail with ValueError."""
        from orxtra.scheduler._executor import Scheduler

        from .conftest import (
            TEST_RUN_PRINCIPAL_ID,
            MockTraceWriter,
            MockTransport,
            make_categories,
        )

        agent = _agent("coder", ["#mutatin"])
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)

        with pytest.raises(
            ValueError,
            match=r"Agent 'coder' references unknown tag 'mutatin'",
        ):
            Scheduler(
                run_principal_id=TEST_RUN_PRINCIPAL_ID,
                trace_writer=trace,  # type: ignore[arg-type]
                transport_registry={"anthropic": transport},  # type: ignore[dict-item]
                agents={"coder": agent},
                categories=make_categories(),
                run_id=uuid6.uuid7(),
                read_root=tmp_path,
                autonomy_level="max",
            )

    def test_custom_tool_in_allow_passes(
        self, tmp_path: Path,
    ) -> None:
        """Custom tools registered via ToolEntry are visible
        to validation."""
        from orxtra.scheduler._executor import Scheduler

        from .conftest import (
            TEST_RUN_PRINCIPAL_ID,
            MockTraceWriter,
            MockTransport,
            make_categories,
        )

        agent = _agent("coder", ["my_custom"])
        custom = [ToolEntry(
            name="my_custom",
            namespace="custom.test",
            tags=frozenset({"readonly"}),
            factory=lambda deps: _make_dummy_tool(
                "my_custom",
            ),
        )]
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)

        # Should not raise.
        Scheduler(
            run_principal_id=TEST_RUN_PRINCIPAL_ID,
            trace_writer=trace,  # type: ignore[arg-type]
            transport_registry={"anthropic": transport},  # type: ignore[dict-item]
            agents={"coder": agent},
            categories=make_categories(),
            run_id=uuid6.uuid7(),
            read_root=tmp_path,
            custom_tools=custom,
            autonomy_level="max",
        )

    def test_unknown_deferred_fails_at_construction(
        self, tmp_path: Path,
    ) -> None:
        """An unknown deferred declaration causes Scheduler
        construction to fail with ValueError."""
        from orxtra.scheduler._executor import Scheduler

        from .conftest import (
            TEST_RUN_PRINCIPAL_ID,
            MockTraceWriter,
            MockTransport,
            make_categories,
        )

        agent = Agent(
            name="coder",
            description="Test agent",
            prompt="You are a test agent.",
            category="default",
            allow=["read"],
            deferred=["nonexistent_tool"],
        )
        trace = MockTraceWriter()
        transport = MockTransport(auto_execute_tools=True)

        with pytest.raises(
            ValueError,
            match=(
                r"Agent 'coder' declares unknown "
                r"deferred tool 'nonexistent_tool'"
            ),
        ):
            Scheduler(
                run_principal_id=TEST_RUN_PRINCIPAL_ID,
                trace_writer=trace,  # type: ignore[arg-type]
                transport_registry={"anthropic": transport},  # type: ignore[dict-item]
                agents={"coder": agent},
                categories=make_categories(),
                run_id=uuid6.uuid7(),
                read_root=tmp_path,
                autonomy_level="max",
            )
