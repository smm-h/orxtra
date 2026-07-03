"""Factory for building Tool instances from HttpExecution definitions.

Takes a DataToolDefinition with an HttpExecution config and builds a
concrete Tool whose execute function:

1. Validates agent-supplied args against the param schema.
2. Substitutes ``{{secret:NAME}}`` placeholders in URL, headers, and
   body_template at CALL TIME (real values never enter the Tool
   object or LLM-visible text).
3. Interpolates ``{param_name}`` in the URL with URL-encoded arg
   values, validating against param patterns when defined.
4. Makes the HTTP request via httpx.
5. Validates the response body against the output schema (if defined).
6. Returns a ToolOutput with validated/projected data and rendered text.
"""

from __future__ import annotations

import json
import re
import time
import urllib.parse
from typing import TYPE_CHECKING, Any

import httpx

from orxtra.protocols import Tool, ToolError, ToolOutput
from orxtra.tool._data_tool_types import DataToolDefinition, HttpExecution, ParamDef

if TYPE_CHECKING:
    from orxtra.secrets import SecretRegistry

# Matches {param_name} placeholders in URL templates.
_PARAM_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _build_json_schema_params(
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


def _substitute_secrets(
    text: str,
    secret_registry: SecretRegistry | None,
) -> str:
    """Replace ``{{secret:NAME}}`` placeholders with real values.

    Hard error if placeholders exist but no registry is provided.
    """
    if "{{secret:" not in text:
        return text
    if secret_registry is None:
        msg = (
            "Definition contains {{secret:...}} placeholders but no "
            "SecretRegistry was provided"
        )
        raise ToolError(msg)
    return secret_registry.substitute(text)


def _interpolate_url(
    url_template: str,
    args: dict[str, Any],
    params: dict[str, ParamDef],
) -> str:
    """Substitute ``{param_name}`` in the URL with URL-encoded arg values.

    Validates interpolated param values against their ``pattern`` if one
    is defined.  Raises ToolError on missing params or pattern mismatches.
    """
    used_params: set[str] = set()

    def _replace(match: re.Match[str]) -> str:
        name = match.group(1)
        if name not in args:
            msg = f"URL parameter {{{name}}} requires argument '{name}' but it was not provided"
            raise ToolError(msg)
        used_params.add(name)
        value = str(args[name])

        # Validate against pattern if defined.
        pdef = params.get(name)
        if pdef is not None and pdef.pattern is not None:
            if not re.fullmatch(pdef.pattern, value):
                msg = (
                    f"Parameter '{name}' value {value!r} does not match "
                    f"pattern {pdef.pattern!r}"
                )
                raise ToolError(msg)

        return urllib.parse.quote(value, safe="")

    return _PARAM_PATTERN.sub(_replace, url_template)


def _validate_output_schema(
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
        path = ".".join(str(p) for p in exc.absolute_path) if exc.absolute_path else "(root)"
        msg = (
            f"Response validation failed at '{path}': {exc.message}"
        )
        raise ToolError(msg) from exc


def _validate_args(
    args: dict[str, Any],
    params: dict[str, ParamDef],
) -> None:
    """Validate that required params are present and types match.

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


def build_http_tool(
    definition: DataToolDefinition,
    secret_registry: SecretRegistry | None = None,
    timeout_ceiling: int = 30,
    preview_threshold: int = 50000,
    preview_lines: int = 50,
) -> Tool:
    """Build a Tool from a DataToolDefinition with HttpExecution config.

    Args:
        definition: A validated DataToolDefinition with ``type = "http"``.
        secret_registry: For call-time secret substitution. Hard error
            if the definition contains ``{{secret:...}}`` placeholders
            and this is None.
        timeout_ceiling: Maximum HTTP timeout in seconds.
        preview_threshold: Byte threshold for response preview.
        preview_lines: Number of head/tail lines in preview.

    Returns:
        A Tool instance ready for execution pipeline wrapping.
    """
    exec_cfg = definition.execution
    if not isinstance(exec_cfg, HttpExecution):
        msg = (
            f"Expected HttpExecution config, got {type(exec_cfg).__name__}"
        )
        raise TypeError(msg)

    # Capture definition values in closure -- real secrets never
    # enter the Tool object or its LLM-visible description/parameters.
    method = exec_cfg.method
    url_template = exec_cfg.url
    header_template = dict(exec_cfg.headers) if exec_cfg.headers else None
    body_tmpl = exec_cfg.body_template
    params = dict(definition.params)
    output_schema = definition.output.schema_ if definition.output else None

    # Build the LLM-visible parameter schema (no secret values).
    parameters = _build_json_schema_params(params)

    # Derive effect tags from method.
    if method in {"GET", "HEAD"}:
        tags = frozenset({"readonly"})
    else:
        tags = frozenset({"mutation"})

    async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
        # 1. Validate args.
        _validate_args(args, params)

        # 2. Definition-level secret substitution at CALL TIME.
        effective_url = _substitute_secrets(url_template, secret_registry)
        effective_headers: dict[str, str] | None = None
        if header_template is not None:
            effective_headers = {
                k: _substitute_secrets(v, secret_registry)
                for k, v in header_template.items()
            }
        effective_body: str | None = None
        if body_tmpl is not None:
            effective_body = _substitute_secrets(body_tmpl, secret_registry)

        # 3. Parameter interpolation in URL.
        effective_url = _interpolate_url(effective_url, args, params)

        # 4. Parameter interpolation in body template (non-URL-encoded).
        if effective_body is not None:
            for param_name, param_value in args.items():
                effective_body = effective_body.replace(
                    f"{{{param_name}}}", str(param_value),
                )

        # 5. Make the HTTP request.
        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=effective_url,
                    headers=effective_headers,
                    content=effective_body,
                    timeout=timeout_ceiling,
                )
        except httpx.TimeoutException:
            msg = f"Request timed out after {timeout_ceiling}s"
            raise ToolError(msg) from None
        except httpx.RequestError as exc:
            msg = f"Request failed: {exc}"
            raise ToolError(msg) from exc
        elapsed_ms = round((time.monotonic() - start) * 1000)

        # 6. Parse response body.
        response_body = response.text
        try:
            response_data = json.loads(response_body)
        except (json.JSONDecodeError, ValueError):
            # Non-JSON response -- treat as plain text.
            response_data = response_body

        # 7. Validate against output schema if defined.
        if output_schema is not None:
            _validate_output_schema(response_data, output_schema)

        # 8. Preview large responses.
        from orxtra.tool._preview import check_and_preview  # noqa: PLC0415

        preview_result = check_and_preview(
            response_body, preview_threshold, preview_lines,
        )

        # 9. Build result.
        result_dict: dict[str, Any] = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": preview_result.content,
            "elapsed_ms": elapsed_ms,
        }

        return ToolOutput(
            data=response_data,
            text=json.dumps(result_dict),
        )

    return Tool(
        name=definition.name,
        description=definition.description,
        parameters=parameters,
        execute=execute,
        namespace=definition.namespace,
        tags=tags,
        deferred=definition.deferred,
    )
