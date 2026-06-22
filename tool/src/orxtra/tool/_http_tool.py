"""HTTP tool constructor for the orxtra tool module."""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

import httpx
from orxtra.protocols._tool import Tool, ToolError
from orxtra.tool._preview import check_and_preview
from orxtra.tool._validation import validate_args


def make_http_tool(
    allowed_hosts: list[str] | str,
    timeout_ceiling: int = 30,
    preview_threshold: int = 50000,
    preview_lines: int = 50,
    consult_mode: bool = False,
) -> Tool:
    """Construct the HTTP tool.

    Args:
        allowed_hosts: List of allowed hostnames, or ``"allow_all"`` to
            permit any host.
        timeout_ceiling: Maximum timeout in seconds. Agent-requested
            timeouts are capped at this value.
        preview_threshold: Byte threshold above which response body is
            previewed.
        preview_lines: Number of head/tail lines in a preview.
        consult_mode: When True, restrict methods to GET and HEAD only.

    Returns:
        A Tool instance for HTTP requests.
    """
    methods = ["GET", "HEAD"] if consult_mode else [
        "GET", "POST", "PUT", "DELETE", "PATCH", "HEAD",
    ]

    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "method": {"type": "string", "enum": methods},
            "url": {"type": "string"},
            "headers": {
                "type": "object",
                "additionalProperties": {"type": "string"},
            },
            "body": {"type": "string"},
            "timeout": {"type": "integer", "minimum": 1},
        },
        "required": ["method", "url"],
        "additionalProperties": False,
    }

    async def execute(args: dict[str, Any]) -> str:
        validate_args(args, schema)

        method: str = args["method"]
        url: str = args["url"]
        headers: dict[str, str] | None = args.get("headers")
        body: str | None = args.get("body")
        requested_timeout: int | None = args.get("timeout")

        parsed = urllib.parse.urlparse(url)
        hostname = parsed.hostname
        if not hostname:
            msg = f"Cannot extract hostname from URL: {url}"
            raise ToolError(msg)

        if allowed_hosts != "allow_all":
            assert isinstance(allowed_hosts, list)  # noqa: S101
            if hostname not in allowed_hosts:
                msg = (
                    f"Host '{hostname}' is not in the allowed list: "
                    f"{', '.join(allowed_hosts)}"
                )
                raise ToolError(msg)

        effective_timeout = (
            min(requested_timeout, timeout_ceiling)
            if requested_timeout is not None
            else timeout_ceiling
        )

        start = time.monotonic()
        try:
            async with httpx.AsyncClient() as client:
                response = await client.request(
                    method=method,
                    url=url,
                    headers=headers,
                    content=body,
                    timeout=effective_timeout,
                )
        except httpx.TimeoutException:
            msg = f"Request timed out after {effective_timeout}s"
            raise ToolError(msg) from None
        except httpx.RequestError as exc:
            msg = f"Request failed: {exc}"
            raise ToolError(msg) from exc
        elapsed_ms = round((time.monotonic() - start) * 1000)

        response_body = response.text
        preview_result = check_and_preview(
            response_body, preview_threshold, preview_lines,
        )

        result: dict[str, Any] = {
            "status_code": response.status_code,
            "headers": dict(response.headers),
            "body": preview_result.content,
            "elapsed_ms": elapsed_ms,
        }

        return json.dumps(result)

    return Tool(
        name="http",
        description=(
            "Make HTTP requests. Supports GET, POST, PUT, DELETE, PATCH,"
            " and HEAD methods. In consult mode, only GET and HEAD are"
            " available."
        ),
        parameters=schema,
        execute=execute,
    )
