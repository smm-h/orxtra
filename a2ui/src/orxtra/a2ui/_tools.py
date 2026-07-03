from __future__ import annotations

import inspect
import json
from typing import Any, cast

from orxtra.a2ui._engine import TemplateEngine
from orxtra.a2ui._fragments import FragmentLibrary
from orxtra.a2ui._registry import SurfaceRegistry
from orxtra.protocols import Tool, ToolError, ToolOutput
from pydantic import BaseModel, ConfigDict, ValidationError


def _validation_error(e: Exception) -> str:
    return json.dumps({"error": "validation_error", "details": str(e)})


class RenderSurfaceParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    template_name: str
    data: dict[str, Any]


class ComposeSurfaceParams(BaseModel):
    model_config = ConfigDict(strict=True, extra="forbid")
    fragments: list[str]
    data: dict[str, Any]


def make_render_surface_tool(registry: SurfaceRegistry) -> Tool:
    """Create a tool that renders a registered surface template."""

    async def execute(args: dict[str, Any]) -> ToolOutput[list[object]]:
        try:
            params = RenderSurfaceParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e
        try:
            template_fn = registry.get(params.template_name)
        except KeyError as e:
            raise ToolError(
                json.dumps({"error": "template_not_found", "details": str(e)}),
            ) from e
        ops = template_fn(params.data)
        text = json.dumps(
            [op.model_dump() for op in ops],
            default=str,
        )
        return ToolOutput(data=cast(list[object], ops), text=text)

    return Tool(
        name="render_surface",
        description="Render a registered surface template with data bindings.",
        parameters=RenderSurfaceParams.model_json_schema(),
        execute=execute,
        namespace="ui",
    )


def make_compose_surface_tool(fragment_lib: FragmentLibrary) -> Tool:
    """Create a tool that composes fragments into a surface."""

    engine = TemplateEngine()

    async def execute(args: dict[str, Any]) -> ToolOutput[list[object]]:
        try:
            params = ComposeSurfaceParams.model_validate(args)
        except ValidationError as e:
            raise ToolError(_validation_error(e)) from e

        all_components: list[dict[str, Any]] = []
        for fragment_name in params.fragments:
            method = getattr(fragment_lib, fragment_name, None)
            if method is None:
                raise ToolError(
                    json.dumps({
                        "error": "fragment_not_found",
                        "details": f"Fragment {fragment_name!r} not found",
                    }),
                )
            sig = inspect.signature(method)
            components = (
                method(fragment_name) if sig.parameters else method()
            )
            all_components.extend(components)

        ops = engine.populate(
            all_components, params.data,
            surface_id="composed", catalog_id="composed",
        )
        text = json.dumps(
            [op.model_dump() for op in ops],
            default=str,
        )
        return ToolOutput(data=cast(list[object], ops), text=text)

    return Tool(
        name="compose_surface",
        description="Compose UI fragments into a surface with data bindings.",
        parameters=ComposeSurfaceParams.model_json_schema(),
        execute=execute,
        namespace="ui",
    )
