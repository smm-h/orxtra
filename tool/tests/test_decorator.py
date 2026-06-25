"""Tests for the @tool decorator and ToolTemplate."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel

from orxtra.protocols import Tool, ToolError, ToolOutput
from orxtra.tool._decorator import ToolTemplate, tool
from orxtra.tool._renderers import JsonRenderer, TextRenderer


# ---------------------------------------------------------------------------
# Test models
# ---------------------------------------------------------------------------


class GreetParams(BaseModel):
    name: str
    greeting: str = "Hello"


class AddParams(BaseModel):
    a: int
    b: int


# ---------------------------------------------------------------------------
# Test decorated functions
# ---------------------------------------------------------------------------


@tool("greet", "Greet someone.", renderer=TextRenderer())
async def greet(params: GreetParams) -> str:
    return f"{params.greeting}, {params.name}!"


@tool("add", "Add two numbers.", renderer=JsonRenderer())
async def add_numbers(params: AddParams, *, offset: int = 0) -> dict[str, int]:
    return {"result": params.a + params.b + offset}


@tool("suspend", "A suspending tool.", renderer=TextRenderer(), suspending=True)
async def suspending_tool(params: GreetParams) -> str:
    return "suspended"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestToolDecoratorBasic:
    """Basic @tool decorator tests."""

    @pytest.mark.asyncio
    async def test_basic_bind_and_execute(self) -> None:
        """Decorated function binds and executes, producing ToolOutput."""
        t = greet.bind()
        result = await t.execute({"name": "World"})
        assert isinstance(result, ToolOutput)
        assert result.data == "Hello, World!"
        assert result.text == "Hello, World!"

    @pytest.mark.asyncio
    async def test_custom_greeting(self) -> None:
        """Pydantic defaults are respected."""
        t = greet.bind()
        result = await t.execute({"name": "Alice", "greeting": "Hi"})
        assert result.data == "Hi, Alice!"

    def test_produces_tool_template(self) -> None:
        """The decorator produces a ToolTemplate."""
        assert isinstance(greet, ToolTemplate)

    def test_bind_returns_tool(self) -> None:
        """bind() returns a standard Tool dataclass."""
        t = greet.bind()
        assert isinstance(t, Tool)

    def test_tool_name_and_description(self) -> None:
        """Bound Tool preserves name and description."""
        t = greet.bind()
        assert t.name == "greet"
        assert t.description == "Greet someone."


class TestToolDecoratorValidation:
    """Pydantic validation error handling."""

    @pytest.mark.asyncio
    async def test_missing_required_field(self) -> None:
        """Missing required field raises ToolError."""
        t = greet.bind()
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await t.execute({})

    @pytest.mark.asyncio
    async def test_wrong_type(self) -> None:
        """Wrong type raises ToolError."""
        t = add_numbers.bind()
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await t.execute({"a": "not_an_int", "b": 2})

    @pytest.mark.asyncio
    async def test_extra_field_forbidden(self) -> None:
        """Extra fields raise ToolError (Pydantic forbid by default in strict)."""
        # BaseModel without extra='forbid' allows extra fields,
        # so we test with a model that forbids them.
        class StrictParams(BaseModel):
            model_config = {"extra": "forbid"}
            x: int

        @tool("strict", "Strict tool.", renderer=TextRenderer())
        async def strict_fn(params: StrictParams) -> str:
            return str(params.x)

        t = strict_fn.bind()
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await t.execute({"x": 1, "extra": "bad"})


class TestToolDecoratorRenderer:
    """Renderer integration tests."""

    @pytest.mark.asyncio
    async def test_text_renderer(self) -> None:
        """TextRenderer produces plain text."""
        t = greet.bind()
        result = await t.execute({"name": "Test"})
        assert result.text == "Hello, Test!"

    @pytest.mark.asyncio
    async def test_json_renderer(self) -> None:
        """JsonRenderer produces JSON text."""
        t = add_numbers.bind()
        result = await t.execute({"a": 1, "b": 2})
        assert result.data == {"result": 3}
        assert '"result": 3' in result.text


class TestToolDecoratorDependencies:
    """Dependency binding tests."""

    @pytest.mark.asyncio
    async def test_dependencies_passed_to_function(self) -> None:
        """bind() passes deps as kwargs to the decorated function."""
        t = add_numbers.bind(offset=10)
        result = await t.execute({"a": 1, "b": 2})
        assert result.data == {"result": 13}

    @pytest.mark.asyncio
    async def test_no_dependencies(self) -> None:
        """Function with no deps works with bare bind()."""
        t = greet.bind()
        result = await t.execute({"name": "Dep"})
        assert result.data == "Hello, Dep!"

    @pytest.mark.asyncio
    async def test_dependency_default_used(self) -> None:
        """Function kwarg defaults are used when dep not bound."""
        t = add_numbers.bind()
        result = await t.execute({"a": 3, "b": 4})
        assert result.data == {"result": 7}


class TestToolDecoratorSuspending:
    """Suspending flag tests."""

    def test_suspending_true_preserved(self) -> None:
        """suspending=True on decorator is preserved on bound Tool."""
        t = suspending_tool.bind()
        assert t.suspending is True

    def test_suspending_false_by_default(self) -> None:
        """Non-suspending tools have suspending=False."""
        t = greet.bind()
        assert t.suspending is False


class TestToolDecoratorSchema:
    """Schema generation tests."""

    def test_schema_matches_model(self) -> None:
        """Bound Tool's parameters match the Pydantic model_json_schema."""
        t = greet.bind()
        expected = GreetParams.model_json_schema()
        assert t.parameters == expected

    def test_schema_has_required_fields(self) -> None:
        """Schema includes required fields from the model."""
        t = greet.bind()
        assert "required" in t.parameters
        assert "name" in t.parameters["required"]

    def test_schema_has_properties(self) -> None:
        """Schema includes properties from the model."""
        t = add_numbers.bind()
        props = t.parameters["properties"]
        assert "a" in props
        assert "b" in props


class TestToolDecoratorNameOverride:
    """Tests for the name override parameter on bind()."""

    def test_name_override_on_bound_tool(self) -> None:
        """bind(name='custom') produces a Tool with name='custom'."""
        t = greet.bind(name="custom_greet")
        assert t.name == "custom_greet"

    def test_name_override_none_uses_template_name(self) -> None:
        """bind(name=None) falls back to the template's name."""
        t = greet.bind(name=None)
        assert t.name == "greet"

    def test_name_override_omitted_uses_template_name(self) -> None:
        """bind() without name uses the template's name."""
        t = greet.bind()
        assert t.name == "greet"

    @pytest.mark.asyncio
    async def test_name_override_does_not_affect_execution(self) -> None:
        """Overriding name does not change the tool's behavior."""
        t = greet.bind(name="renamed")
        result = await t.execute({"name": "World"})
        assert result.data == "Hello, World!"

    def test_name_override_with_deps(self) -> None:
        """name override works alongside bound dependencies."""
        t = add_numbers.bind(name="adder", offset=5)
        assert t.name == "adder"


class TestToolDecoratorMultipleBind:
    """Multiple binds from the same template."""

    @pytest.mark.asyncio
    async def test_independent_binds(self) -> None:
        """Same template can produce multiple independent Tools."""
        t1 = add_numbers.bind(offset=0)
        t2 = add_numbers.bind(offset=100)

        r1 = await t1.execute({"a": 1, "b": 1})
        r2 = await t2.execute({"a": 1, "b": 1})

        assert r1.data == {"result": 2}
        assert r2.data == {"result": 102}

    def test_binds_are_distinct_tools(self) -> None:
        """Each bind() returns a distinct Tool instance."""
        t1 = greet.bind()
        t2 = greet.bind()
        assert t1 is not t2


class TestToolDecoratorErrorPropagation:
    """ToolError propagation tests."""

    @pytest.mark.asyncio
    async def test_tool_error_propagates(self) -> None:
        """ToolError raised inside the function propagates unchanged."""

        class SimpleParams(BaseModel):
            x: int

        @tool("fail", "Always fails.", renderer=TextRenderer())
        async def failing_tool(params: SimpleParams) -> str:
            msg = "Something went wrong"
            raise ToolError(msg)

        t = failing_tool.bind()
        with pytest.raises(ToolError, match="Something went wrong"):
            await t.execute({"x": 1})

    @pytest.mark.asyncio
    async def test_unexpected_error_propagates(self) -> None:
        """Non-ToolError exceptions propagate as-is (not swallowed)."""

        class SimpleParams(BaseModel):
            x: int

        @tool("boom", "Raises ValueError.", renderer=TextRenderer())
        async def boom_tool(params: SimpleParams) -> str:
            msg = "unexpected"
            raise ValueError(msg)

        t = boom_tool.bind()
        with pytest.raises(ValueError, match="unexpected"):
            await t.execute({"x": 1})


class TestToolDecoratorEdgeCases:
    """Edge cases and error handling for the decorator itself."""

    def test_no_params_raises_type_error(self) -> None:
        """Decorating a function with no parameters raises TypeError."""
        with pytest.raises(TypeError, match="must have at least one parameter"):

            @tool("bad", "No params.", renderer=TextRenderer())
            async def no_params() -> str:
                return "oops"

    def test_unannotated_first_param_raises_type_error(self) -> None:
        """First parameter without type annotation raises TypeError."""
        with pytest.raises(TypeError, match="must have a type annotation"):

            @tool("bad", "No annotation.", renderer=TextRenderer())
            async def no_annotation(params) -> str:  # type: ignore[no-untyped-def]
                return "oops"

    def test_non_basemodel_first_param_raises_type_error(self) -> None:
        """First parameter typed as non-BaseModel raises TypeError."""
        with pytest.raises(TypeError, match="must be typed as a Pydantic BaseModel"):

            @tool("bad", "Wrong type.", renderer=TextRenderer())
            async def wrong_type(params: dict[str, Any]) -> str:
                return "oops"

    def test_template_name_attribute(self) -> None:
        """ToolTemplate exposes name attribute."""
        assert greet.name == "greet"

    def test_template_description_attribute(self) -> None:
        """ToolTemplate exposes description attribute."""
        assert greet.description == "Greet someone."


class TestToolDecoratorNamespaceTags:
    """Namespace and tags flow through @tool -> ToolTemplate -> bind -> Tool."""

    def test_namespace_and_tags_on_decorator(self) -> None:
        """@tool with namespace and tags stores them on the template."""

        @tool(
            "ns_tool",
            "A namespaced tool.",
            renderer=TextRenderer(),
            namespace="fs.read",
            tags=frozenset({"readonly"}),
        )
        async def ns_tool(params: GreetParams) -> str:
            return "ok"

        assert ns_tool._namespace == "fs.read"
        assert ns_tool._tags == frozenset({"readonly"})

    def test_namespace_and_tags_flow_to_bound_tool(self) -> None:
        """Namespace and tags from the decorator appear on the bound Tool."""

        @tool(
            "ns_tool2",
            "A namespaced tool.",
            renderer=TextRenderer(),
            namespace="fs.write",
            tags=frozenset({"mutation"}),
        )
        async def ns_tool2(params: GreetParams) -> str:
            return "ok"

        t = ns_tool2.bind()
        assert t.namespace == "fs.write"
        assert t.tags == frozenset({"mutation"})

    def test_namespace_default_empty(self) -> None:
        """Namespace defaults to empty string."""
        t = greet.bind()
        assert t.namespace == ""

    def test_tags_default_empty(self) -> None:
        """Tags default to empty frozenset."""
        t = greet.bind()
        assert t.tags == frozenset()

    def test_namespace_override_at_bind_time(self) -> None:
        """bind(namespace=...) overrides the template's namespace."""

        @tool(
            "ns_override",
            "Override test.",
            renderer=TextRenderer(),
            namespace="original",
        )
        async def ns_override(params: GreetParams) -> str:
            return "ok"

        t = ns_override.bind(namespace="overridden")
        assert t.namespace == "overridden"

    def test_tags_override_at_bind_time(self) -> None:
        """bind(tags=...) overrides the template's tags."""

        @tool(
            "tags_override",
            "Override test.",
            renderer=TextRenderer(),
            tags=frozenset({"original"}),
        )
        async def tags_override(params: GreetParams) -> str:
            return "ok"

        t = tags_override.bind(tags=frozenset({"overridden"}))
        assert t.tags == frozenset({"overridden"})

    def test_namespace_override_none_uses_template(self) -> None:
        """bind(namespace=None) falls back to the template's namespace."""

        @tool(
            "ns_none",
            "None test.",
            renderer=TextRenderer(),
            namespace="keep_me",
        )
        async def ns_none(params: GreetParams) -> str:
            return "ok"

        t = ns_none.bind(namespace=None)
        assert t.namespace == "keep_me"

    def test_tags_override_none_uses_template(self) -> None:
        """bind(tags=None) falls back to the template's tags."""

        @tool(
            "tags_none",
            "None test.",
            renderer=TextRenderer(),
            tags=frozenset({"keep_me"}),
        )
        async def tags_none(params: GreetParams) -> str:
            return "ok"

        t = tags_none.bind(tags=None)
        assert t.tags == frozenset({"keep_me"})

    def test_namespace_and_tags_with_name_override(self) -> None:
        """Namespace/tags work alongside name override and deps."""

        @tool(
            "combo",
            "Combo test.",
            renderer=TextRenderer(),
            namespace="exec",
            tags=frozenset({"mutation"}),
        )
        async def combo(params: AddParams, *, offset: int = 0) -> dict[str, int]:
            return {"result": params.a + params.b + offset}

        t = combo.bind(name="custom", namespace="exec.custom", offset=5)
        assert t.name == "custom"
        assert t.namespace == "exec.custom"
        assert t.tags == frozenset({"mutation"})
