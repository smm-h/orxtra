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
from orxtra.tool._data_tool_shared import (
    build_json_schema_params,
    validate_args,
    validate_output_schema,
)
from orxtra.tool._data_tool_types import DataToolDefinition, HttpExecution, ParamDef

if TYPE_CHECKING:
    from orxtra.protocols import ToolDeps
    from orxtra.secrets import SecretRegistry

# Matches {param_name} placeholders in URL templates.
_PARAM_PATTERN = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*)\}")


def _build_json_schema_params(
    params: dict[str, ParamDef],
) -> dict[str, Any]:
    """Build a JSON Schema ``parameters`` dict from ParamDef entries.

    Delegates to the shared implementation in ``_data_tool_shared``.
    """
    return build_json_schema_params(params)


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
            msg = (
                f"URL parameter {{{name}}} requires argument "
                f"'{name}' but it was not provided"
            )
            raise ToolError(msg)
        used_params.add(name)
        value = str(args[name])

        # Validate against pattern if defined.
        pdef = params.get(name)
        if (
            pdef is not None
            and pdef.pattern is not None
            and not re.fullmatch(pdef.pattern, value)
        ):
            msg = (
                f"Parameter '{name}' value {value!r} does not match "
                f"pattern {pdef.pattern!r}"
            )
            raise ToolError(msg)

        return urllib.parse.quote(value, safe="")

    return _PARAM_PATTERN.sub(_replace, url_template)


def _validate_output_schema(
    response_data: Any,
    schema: dict[str, Any],
) -> None:
    """Validate response data against the output JSON Schema.

    Delegates to the shared implementation in ``_data_tool_shared``.
    """
    validate_output_schema(response_data, schema)


def _validate_args(
    args: dict[str, Any],
    params: dict[str, ParamDef],
) -> None:
    """Validate that required params are present and types match.

    Delegates to the shared implementation in ``_data_tool_shared``.
    """
    validate_args(args, params)


def _apply_secret_substitution(
    url_template: str,
    header_template: dict[str, str] | None,
    body_tmpl: str | None,
    secret_registry: SecretRegistry | None,
) -> tuple[str, dict[str, str] | None, str | None]:
    """Apply secret substitution to URL, headers, and body template.

    Returns (effective_url, effective_headers, effective_body).
    """
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
    return effective_url, effective_headers, effective_body


async def _make_http_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None,
    body: str | None,
    timeout_ceiling: int,
) -> tuple[httpx.Response, int]:
    """Execute the HTTP request. Returns (response, elapsed_ms)."""
    start = time.monotonic()
    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method=method,
                url=url,
                headers=headers,
                content=body,
                timeout=timeout_ceiling,
            )
    except httpx.TimeoutException:
        msg = f"Request timed out after {timeout_ceiling}s"
        raise ToolError(msg) from None
    except httpx.RequestError as exc:
        msg = f"Request failed: {exc}"
        raise ToolError(msg) from exc
    elapsed_ms = round((time.monotonic() - start) * 1000)
    return response, elapsed_ms


def build_http_tool(
    definition: DataToolDefinition,
    deps: ToolDeps,
    timeout_ceiling: int = 30,
) -> Tool:
    """Build a Tool from a DataToolDefinition with HttpExecution config.

    Args:
        definition: A validated DataToolDefinition with ``type = "http"``.
        deps: Session-scoped dependencies (secret_registry,
            preview_threshold, preview_lines).
        timeout_ceiling: Maximum HTTP timeout in seconds.

    Returns:
        A Tool instance ready for execution pipeline wrapping.
    """
    secret_registry = deps.secret_registry
    preview_threshold = deps.preview_threshold
    preview_lines = deps.preview_lines
    exec_cfg = definition.execution
    if not isinstance(exec_cfg, HttpExecution):
        msg = (
            f"Expected HttpExecution config, "
            f"got {type(exec_cfg).__name__}"
        )
        raise TypeError(msg)

    # Capture definition values in closure -- real secrets never
    # enter the Tool object or its LLM-visible description/parameters.
    method = exec_cfg.method
    url_template = exec_cfg.url
    header_template = (
        dict(exec_cfg.headers) if exec_cfg.headers else None
    )
    body_tmpl = exec_cfg.body_template
    params = dict(definition.params)
    output_schema = (
        definition.output.schema_ if definition.output else None
    )

    # Build the LLM-visible parameter schema (no secret values).
    parameters = _build_json_schema_params(params)

    # Derive effect tags from method.
    if method in {"GET", "HEAD"}:
        tags = frozenset({"readonly"})
    else:
        tags = frozenset({"mutation"})

    async def execute(args: dict[str, Any]) -> ToolOutput[Any]:
        _validate_args(args, params)

        # Secret substitution at CALL TIME.
        effective_url, effective_headers, effective_body = (
            _apply_secret_substitution(
                url_template, header_template, body_tmpl,
                secret_registry,
            )
        )

        # Parameter interpolation in URL.
        effective_url = _interpolate_url(
            effective_url, args, params,
        )

        # Parameter interpolation in body (non-URL-encoded).
        if effective_body is not None:
            for pname, pvalue in args.items():
                effective_body = effective_body.replace(
                    f"{{{pname}}}", str(pvalue),
                )

        # HTTP request.
        response, elapsed_ms = await _make_http_request(
            method=method,
            url=effective_url,
            headers=effective_headers,
            body=effective_body,
            timeout_ceiling=timeout_ceiling,
        )

        # Parse and validate response.
        response_body = response.text
        try:
            response_data: Any = json.loads(response_body)
        except (json.JSONDecodeError, ValueError):
            response_data = response_body

        if output_schema is not None:
            _validate_output_schema(response_data, output_schema)

        from orxtra.tool._preview import (
            check_and_preview,
        )

        preview = check_and_preview(
            response_body, preview_threshold, preview_lines,
        )

        result_dict: dict[str, Any] = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": preview.content,
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
