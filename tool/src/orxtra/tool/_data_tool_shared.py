"""Shared utilities for data-defined tool execution factories.

Functions here are used by both ``_data_tool_http`` and
``_data_tool_monty`` (and any future execution-type factories).
"""

from __future__ import annotations

from typing import Any

from orxtra.protocols import ToolError
from orxtra.tool._data_tool_types import ParamDef


def build_json_schema_params(
    params: dict[str, ParamDef],
) -> dict[str, Any]:
    """Build a JSON Schema ``parameters`` dict from ParamDef entries.

    This is the schema the LLM sees for tool-call arguments.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    type_map = {
        "string": "string",
        "integer": "integer",
        "number": "number",
        "boolean": "boolean",
    }

    for name, pdef in params.items():
        prop: dict[str, Any] = {
            "type": type_map[pdef.type],
            "description": pdef.description,
        }
        if pdef.pattern is not None:
            prop["pattern"] = pdef.pattern
        properties[name] = prop
        if pdef.required:
            required.append(name)

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    schema["additionalProperties"] = False
    return schema


def validate_args(
    args: dict[str, Any],
    params: dict[str, ParamDef],
) -> None:
    """Validate that required params are present and no unexpected args.

    Raises ToolError on missing required params or unexpected args.
    """
    # Check for unexpected args.
    known = set(params.keys())
    unexpected = set(args.keys()) - known
    if unexpected:
        msg = f"Unexpected arguments: {sorted(unexpected)}"
        raise ToolError(msg)

    # Check required params.
    for name, pdef in params.items():
        if pdef.required and name not in args:
            msg = f"Missing required argument: '{name}'"
            raise ToolError(msg)


def validate_output_schema(
    response_data: Any,  # noqa: ANN401
    schema: dict[str, Any],
) -> None:
    """Validate response data against the output JSON Schema.

    Raises ToolError with a descriptive message on validation failure.
    """
    import jsonschema  # noqa: PLC0415

    try:
        jsonschema.validate(instance=response_data, schema=schema)
    except jsonschema.ValidationError as exc:
        # Build a descriptive message naming the failing field.
        path = (
            ".".join(str(p) for p in exc.absolute_path)
            if exc.absolute_path
            else "(root)"
        )
        msg = (
            f"Output validation failed at '{path}': {exc.message}"
        )
        raise ToolError(msg) from exc
