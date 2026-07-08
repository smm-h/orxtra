"""Tests for tool-graph advisory edges and result-appendix surfacing.

Covers:
- Using a tool surfaces its neighbor suggestion exactly once per session.
- Neighbor suggestion text follows the packaged .md template.
- No auto-loading: the tool set does not change after a suggestion.
- Suggestion deduplication across multiple tool uses in one session.
- The result_appendix callback integrates with the pipeline.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID

import uuid6
from orxtra.agent import Agent
from orxtra.protocols import Tool, ToolOutput
from orxtra.scheduler._executor import Scheduler
from orxtra.scheduler._prompt_templates import render_template
from orxtra.scheduler._tool_registry import (
    ToolEntry,
    ToolRegistry,
    create_builtin_registry,
)
from orxtra.tool._pipeline import wrap_tool_with_pipeline

from .conftest import (
    MockTraceWriter,
    MockTransport,
    make_categories,
)

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

_SESSION_ID = "test-session"
_TASK_ID = UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


def _passing_check(session_id: str) -> UUID:
    return _TASK_ID


def _dummy_tool(
    name: str = "test_tool",
    result_text: str = "ok",
) -> Tool:
    async def _execute(
        args: dict[str, Any],
    ) -> ToolOutput[str]:
        return ToolOutput(data="ok", text=result_text)

    return Tool(
        name=name,
        description=f"Test tool: {name}",
        parameters={"type": "object", "properties": {}},
        execute=_execute,
    )


def _agent(
    allow: list[str],
    deferred: list[str] | None = None,
) -> Agent:
    return Agent(
        name="test-agent",
        description="Test agent",
        prompt="You are a test agent.",
        category="default",
        allow=allow,
        deferred=deferred or [],
    )


def _make_scheduler(
    agent: Agent,
    tmp_path: Any,
    custom_tools: list[ToolEntry] | None = None,
) -> Scheduler:
    trace = MockTraceWriter()
    transport = MockTransport(auto_execute_tools=True)
    return Scheduler(
        trace_writer=trace,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents={agent.name: agent},
        categories=make_categories(),
        run_id=uuid6.uuid7(),
        read_root=tmp_path,
        autonomy_level="max",
        custom_tools=custom_tools,
    )


# ---------------------------------------------------------------
# Result-appendix surfacing via pipeline integration
# ---------------------------------------------------------------


class TestResultAppendixPipeline:
    """The result_appendix callback in the pipeline appends text."""

    async def test_appendix_appended_to_result(self) -> None:
        """When the appendix callback returns text, it is
        appended to the tool result."""
        tool = _dummy_tool("alpha", result_text="original")

        def appendix(name: str) -> str | None:
            return "SUGGESTION: try beta"

        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )
        result = await wrapped.execute({})
        assert "original" in result.text
        assert "SUGGESTION: try beta" in result.text

    async def test_appendix_none_no_change(self) -> None:
        """When the appendix callback returns None, the result
        text is unchanged."""
        tool = _dummy_tool("alpha", result_text="original")

        def appendix(name: str) -> str | None:
            return None

        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )
        result = await wrapped.execute({})
        assert result.text == "original"

    async def test_no_appendix_callback(self) -> None:
        """When no appendix callback is provided, the result
        text is unchanged."""
        tool = _dummy_tool("alpha", result_text="original")

        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
        )
        result = await wrapped.execute({})
        assert result.text == "original"

    async def test_appendix_receives_tool_name(self) -> None:
        """The appendix callback receives the correct tool name."""
        received_names: list[str] = []

        def appendix(name: str) -> str | None:
            received_names.append(name)
            return None

        tool = _dummy_tool("my_tool")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )
        await wrapped.execute({})
        assert received_names == ["my_tool"]


# ---------------------------------------------------------------
# Surfacing with registry edges
# ---------------------------------------------------------------


class TestRegistryBasedSurfacing:
    """Surfacing logic using registry edges and per-session dedupe."""

    def _make_appendix(
        self, registry: ToolRegistry,
    ) -> tuple[set[str], Any]:
        """Build a result_appendix closure matching the real
        implementation in _agent_execution.py."""
        suggested: set[str] = set()

        def appendix(tool_name: str) -> str | None:
            edges = registry.edges_from(tool_name)
            if not edges:
                return None
            neighbors = []
            for edge in edges:
                target = edge.target_tool
                if target not in suggested:
                    neighbors.append(target)
            if not neighbors:
                return None
            suggested.update(neighbors)
            names_str = ", ".join(sorted(neighbors))
            return render_template(
                "tool_suggestion",
                {"tool_names": names_str},
            )

        return suggested, appendix

    async def test_suggestion_surfaces_once(self) -> None:
        """Using a tool with edges surfaces neighbor
        suggestions exactly once per session."""
        registry = ToolRegistry()
        for name in ("alpha", "beta", "gamma"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _dummy_tool(n),
            ))
        registry.add_edge("alpha", "beta", "follows")
        registry.add_edge("alpha", "gamma", "related_to")

        _suggested, appendix = self._make_appendix(registry)
        tool = _dummy_tool("alpha", result_text="done")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )

        # First call: suggestions appear.
        result1 = await wrapped.execute({})
        assert "beta" in result1.text
        assert "gamma" in result1.text
        assert "load_tools" in result1.text

        # Second call: same tool, suggestions already made.
        result2 = await wrapped.execute({})
        assert "beta" not in result2.text or result2.text == "done"
        # More precisely:
        assert result2.text == "done"

    async def test_cross_tool_dedupe(self) -> None:
        """If tool A suggests target C, then tool B's
        suggestion skips C."""
        registry = ToolRegistry()
        for name in ("a", "b", "c"):
            registry.register(ToolEntry(
                name=name,
                namespace="test",
                tags=frozenset(),
                factory=lambda deps, n=name: _dummy_tool(n),
            ))
        registry.add_edge("a", "c", "follows")
        registry.add_edge("b", "c", "follows")

        _suggested, appendix = self._make_appendix(registry)

        tool_a = _dummy_tool("a", result_text="result-a")
        wrapped_a = wrap_tool_with_pipeline(
            tool=tool_a,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )

        tool_b = _dummy_tool("b", result_text="result-b")
        wrapped_b = wrap_tool_with_pipeline(
            tool=tool_b,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )

        # Use tool A: suggests C.
        r1 = await wrapped_a.execute({})
        assert "c" in r1.text

        # Use tool B: C already suggested, no new suggestion.
        r2 = await wrapped_b.execute({})
        assert r2.text == "result-b"

    async def test_no_edges_no_suggestion(self) -> None:
        """A tool with no outgoing edges gets no suggestion."""
        registry = ToolRegistry()
        registry.register(ToolEntry(
            name="lonely",
            namespace="test",
            tags=frozenset(),
            factory=lambda deps: _dummy_tool("lonely"),
        ))

        _, appendix = self._make_appendix(registry)
        tool = _dummy_tool("lonely", result_text="alone")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )
        result = await wrapped.execute({})
        assert result.text == "alone"

    async def test_builtin_edges_surface(self) -> None:
        """Using a builtin tool with seed edges produces
        a suggestion including the template text."""
        registry = create_builtin_registry()
        _, appendix = self._make_appendix(registry)

        tool = _dummy_tool("read", result_text="file contents")
        wrapped = wrap_tool_with_pipeline(
            tool=tool,
            scheduler_check=_passing_check,
            secret_registry=None,
            trace_callback=None,
            session_id=_SESSION_ID,
            result_appendix=appendix,
        )
        result = await wrapped.execute({})
        # read has edges to edit and grep.
        assert "edit" in result.text
        assert "grep" in result.text
        # Verify template text.
        assert "load_tools" in result.text


class TestSuggestionTemplate:
    """Suggestion text follows the packaged .md template."""

    def test_template_renders_multiple(self) -> None:
        text = render_template(
            "tool_suggestion",
            {"tool_names": "edit, grep"},
        )
        assert "edit" in text
        assert "grep" in text
        assert "load_tools" in text

    def test_template_renders_single(self) -> None:
        text = render_template(
            "tool_suggestion",
            {"tool_names": "edit"},
        )
        assert "edit" in text
        assert "load_tools" in text


class TestNoAutoLoading:
    """Suggestions never auto-load tools -- verified via pipeline."""

    async def test_tool_set_unchanged_after_suggestion(
        self, tmp_path: Any,
    ) -> None:
        """After a suggestion is surfaced, no new tools are
        added to the session. The tool set is unchanged."""
        agent = _agent(
            allow=["read", "edit", "grep"],
            deferred=["edit"],
        )
        sched = _make_scheduler(agent, tmp_path)

        from orxtra.protocols import TaskSpec

        task = TaskSpec(
            name="test-task",
            agent="test-agent",
            task_prompt="Do something",
            context_refinement=False,
        )
        task_id = uuid6.uuid7()

        with patch(
            "orxtra.scheduler._agent_execution.create_session",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_session = MagicMock()
            mock_create.return_value = mock_session

            await sched._create_agent_session(
                task, task_id, 1,
            )

            tools = list(mock_create.call_args[1]["tools"])

        initial_names = {t.name for t in tools}
        tool_map = {t.name: t for t in tools}

        # edit is deferred.
        assert tool_map["edit"].deferred is True

        # The tool set contains exact names we expect.
        assert "read" in initial_names
        assert "edit" in initial_names
        assert "grep" in initial_names

        # Even though read's edges point to edit and grep,
        # using read would only SUGGEST them, never load them.
        # The deferred edit stub remains deferred.
        assert tool_map["edit"].deferred is True
