from __future__ import annotations

import pytest
from orxtra.a2ui._engine import TemplateEngine
from orxtra.a2ui._fragments import FragmentLibrary
from orxtra.a2ui._registry import SurfaceRegistry
from orxtra.a2ui._templates import (
    budget_gauge_template,
    default_registry,
    inbox_approval_template,
    task_summary_template,
)
from orxtra.a2ui._tools import make_compose_surface_tool, make_render_surface_tool
from orxtra.protocols import CreateSurface, ToolError, UpdateComponents, UpdateDataModel


class TestSurfaceRegistry:
    def test_register_and_get(self) -> None:
        registry = SurfaceRegistry()

        def my_template(data: dict[str, object]) -> list[object]:
            return []

        registry.register("test", my_template)
        assert registry.get("test") is my_template

    def test_get_missing_raises_keyerror(self) -> None:
        registry = SurfaceRegistry()
        with pytest.raises(KeyError, match="not_found"):
            registry.get("not_found")

    def test_list_templates_sorted(self) -> None:
        registry = SurfaceRegistry()
        registry.register("beta", lambda d: [])
        registry.register("alpha", lambda d: [])
        registry.register("gamma", lambda d: [])
        assert registry.list_templates() == ["alpha", "beta", "gamma"]

    def test_list_templates_empty(self) -> None:
        registry = SurfaceRegistry()
        assert registry.list_templates() == []

    def test_constructor_with_initial_templates(self) -> None:
        fn = lambda d: []  # noqa: E731
        registry = SurfaceRegistry({"t1": fn, "t2": fn})
        assert registry.list_templates() == ["t1", "t2"]

    def test_register_overwrites(self) -> None:
        fn1 = lambda d: []  # noqa: E731
        fn2 = lambda d: [1]  # noqa: E731
        registry = SurfaceRegistry()
        registry.register("t", fn1)
        registry.register("t", fn2)
        assert registry.get("t") is fn2


class TestTemplateEngine:
    def test_populate_returns_three_ops(self) -> None:
        engine = TemplateEngine()
        components = [
            {"id": "c1", "component": "Text", "properties": {"content": "hello"}},
        ]
        ops = engine.populate(components, {"key": "val"})
        assert len(ops) == 3
        assert isinstance(ops[0], CreateSurface)
        assert isinstance(ops[1], UpdateComponents)
        assert isinstance(ops[2], UpdateDataModel)

    def test_populate_resolves_pointer(self) -> None:
        engine = TemplateEngine()
        components = [
            {"id": "c1", "component": "Text", "properties": {"content": "$/name"}},
        ]
        data = {"name": "My Task"}
        ops = engine.populate(components, data)
        update_comp = ops[1]
        assert isinstance(update_comp, UpdateComponents)
        assert update_comp.components[0]["properties"]["content"] == "My Task"

    def test_populate_resolves_nested_pointer(self) -> None:
        engine = TemplateEngine()
        components = [
            {
                "id": "c1",
                "component": "Text",
                "properties": {"content": "$/nested/value"},
            },
        ]
        data = {"nested": {"value": 42}}
        ops = engine.populate(components, data)
        update_comp = ops[1]
        assert isinstance(update_comp, UpdateComponents)
        assert update_comp.components[0]["properties"]["content"] == 42

    def test_populate_non_pointer_passes_through(self) -> None:
        engine = TemplateEngine()
        components = [
            {
                "id": "c1",
                "component": "Text",
                "properties": {"content": "static text"},
            },
        ]
        ops = engine.populate(components, {})
        update_comp = ops[1]
        assert isinstance(update_comp, UpdateComponents)
        assert update_comp.components[0]["properties"]["content"] == "static text"

    def test_populate_custom_surface_id(self) -> None:
        engine = TemplateEngine()
        ops = engine.populate(
            [], {}, surface_id="custom-id", catalog_id="custom-catalog",
        )
        create = ops[0]
        assert isinstance(create, CreateSurface)
        assert create.surface_id == "custom-id"
        assert create.catalog_id == "custom-catalog"

    def test_populate_data_model_contains_full_data(self) -> None:
        engine = TemplateEngine()
        data = {"a": 1, "b": "two"}
        ops = engine.populate([], data)
        dm = ops[2]
        assert isinstance(dm, UpdateDataModel)
        assert dm.value == data
        assert dm.path == "/"

    def test_populate_unresolvable_pointer_returns_none(self) -> None:
        engine = TemplateEngine()
        components = [
            {
                "id": "c1",
                "component": "Text",
                "properties": {"content": "$/missing/path"},
            },
        ]
        ops = engine.populate(components, {"other": "val"})
        update_comp = ops[1]
        assert isinstance(update_comp, UpdateComponents)
        assert update_comp.components[0]["properties"]["content"] is None


class TestFragmentLibrary:
    def test_task_card_structure(self) -> None:
        components = FragmentLibrary.task_card("t1")
        assert len(components) == 1
        card = components[0]
        assert card["id"] == "task-card-t1"
        assert card["component"] == "Card"
        children = card["properties"]["children"]
        assert len(children) == 6
        # All children are Text components with pointer bindings
        for child in children:
            assert child["component"] == "Text"
            assert "content" in child["properties"]
            assert child["properties"]["content"].startswith("$")

    def test_budget_meter_structure(self) -> None:
        components = FragmentLibrary.budget_meter("/budget")
        assert len(components) == 1
        assert components[0]["component"] == "Text"
        assert components[0]["id"] == "budget-meter-budget"

    def test_approval_button_structure(self) -> None:
        components = FragmentLibrary.approval_button("confirm")
        assert len(components) == 1
        btn = components[0]
        assert btn["component"] == "Button"
        assert btn["id"] == "approval-btn-confirm"
        assert btn["properties"]["action"]["type"] == "server"
        assert btn["properties"]["action"]["name"] == "confirm"

    def test_check_result_list_structure(self) -> None:
        components = FragmentLibrary.check_result_list("/checks")
        assert len(components) == 1
        lst = components[0]
        assert lst["component"] == "List"
        assert "items" in lst["properties"]
        assert "item_template" in lst["properties"]

    def test_text_block_structure(self) -> None:
        components = FragmentLibrary.text_block("/message")
        assert len(components) == 1
        txt = components[0]
        assert txt["component"] == "Text"
        assert txt["properties"]["content"] == "$/message"

    def test_event_entry_structure(self) -> None:
        components = FragmentLibrary.event_entry("/events/0")
        assert len(components) == 1
        entry = components[0]
        assert entry["component"] == "Text"
        props = entry["properties"]
        assert "event_type" in props
        assert "timestamp" in props
        assert "summary" in props


class TestInboxApprovalTemplate:
    def test_produces_valid_operations(self) -> None:
        data = {
            "question": "Should we proceed?",
            "options": [
                {"label": "yes"},
                {"label": "no"},
            ],
            "assumed_option": "yes",
            "contradiction_impact": "Work may need reversal",
            "tags": ["important"],
        }
        ops = inbox_approval_template(data)
        assert len(ops) == 3
        assert isinstance(ops[0], CreateSurface)
        assert ops[0].surface_id == "inbox-approval"
        assert isinstance(ops[1], UpdateComponents)
        assert isinstance(ops[2], UpdateDataModel)

    def test_resolves_data_bindings(self) -> None:
        data = {
            "question": "Approve deployment?",
            "options": [{"label": "approve"}, {"label": "reject"}],
            "assumed_option": "approve",
            "contradiction_impact": "Deployment cancelled",
            "tags": [],
        }
        ops = inbox_approval_template(data)
        update = ops[1]
        assert isinstance(update, UpdateComponents)
        card = update.components[0]
        children = card["properties"]["children"]
        # First child is the question text
        question_text = children[0]
        assert question_text["properties"]["content"] == "Approve deployment?"


class TestTaskSummaryTemplate:
    def test_produces_valid_operations(self) -> None:
        data = {
            "name": "compile",
            "state": "running",
            "type": "script",
            "agent": "builder",
            "attempt_count": 2,
            "cost_usd": 0.05,
        }
        ops = task_summary_template(data)
        assert len(ops) == 3
        assert isinstance(ops[0], CreateSurface)
        assert ops[0].surface_id == "task-summary"
        assert isinstance(ops[1], UpdateComponents)
        update = ops[1]
        card = update.components[0]
        children = card["properties"]["children"]
        assert children[0]["properties"]["content"] == "compile"
        assert children[1]["properties"]["content"] == "running"


class TestBudgetGaugeTemplate:
    def test_formats_percentage(self) -> None:
        data = {"spent_usd": 0.5, "budget_usd": 2.0}
        ops = budget_gauge_template(data)
        assert len(ops) == 3
        update = ops[1]
        assert isinstance(update, UpdateComponents)
        assert update.components[0]["properties"]["content"] == "25.0%"


class TestDefaultRegistry:
    def test_has_all_standard_templates(self) -> None:
        templates = default_registry.list_templates()
        assert "inbox_approval" in templates
        assert "task_summary" in templates
        assert "budget_gauge" in templates
        assert "check_verdict" in templates
        assert "event_entry" in templates


class TestRenderSurfaceTool:
    @pytest.mark.asyncio
    async def test_renders_template(self) -> None:
        tool = make_render_surface_tool(default_registry)
        assert tool.name == "render_surface"
        assert tool.namespace == "ui"

        result = await tool.execute({
            "template_name": "task_summary",
            "data": {
                "name": "test",
                "state": "done",
                "type": "script",
                "agent": "tester",
                "attempt_count": 1,
                "cost_usd": 0.01,
            },
        })
        assert result.data is not None
        assert len(result.data) == 3
        assert result.text  # non-empty JSON

    @pytest.mark.asyncio
    async def test_missing_template_raises_tool_error(self) -> None:
        tool = make_render_surface_tool(default_registry)
        with pytest.raises(ToolError, match="template_not_found"):
            await tool.execute({
                "template_name": "nonexistent",
                "data": {},
            })


class TestComposeSurfaceTool:
    @pytest.mark.asyncio
    async def test_composes_fragments(self) -> None:
        lib = FragmentLibrary()
        tool = make_compose_surface_tool(lib)
        assert tool.name == "compose_surface"
        assert tool.namespace == "ui"

        result = await tool.execute({
            "fragments": ["text_block"],
            "data": {"some": "value"},
        })
        assert result.data is not None
        assert len(result.data) == 3
        assert result.text  # non-empty JSON

    @pytest.mark.asyncio
    async def test_missing_fragment_raises_tool_error(self) -> None:
        lib = FragmentLibrary()
        tool = make_compose_surface_tool(lib)
        with pytest.raises(ToolError, match="fragment_not_found"):
            await tool.execute({
                "fragments": ["nonexistent_fragment"],
                "data": {},
            })
