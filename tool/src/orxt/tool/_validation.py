from __future__ import annotations

from typing import Any

import jsonschema  # type: ignore[import-untyped]
from orxt.protocols._tool import ToolError


def validate_args(args: dict[str, Any], schema: dict[str, Any]) -> None:
    """Validate tool arguments against a JSON Schema.

    Args:
        args: The arguments dict from the tool call.
        schema: The JSON Schema to validate against.

    Raises:
        ToolError: If validation fails, with a clear message.
    """
    try:
        jsonschema.validate(instance=args, schema=schema)
    except jsonschema.ValidationError as exc:
        msg = f"Invalid tool arguments: {exc.message}"
        raise ToolError(msg) from exc
