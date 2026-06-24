"""HTTP tool constructor for the orxtra tool module."""

from __future__ import annotations

import json
import time
import urllib.parse
from typing import Any

import httpx
from orxtra.protocols._results import HttpResponse, ToolOutput
from orxtra.protocols._tool import Tool, ToolError
from orxtra.tool._decorator import tool
from orxtra.tool._params import HttpConsultParams, HttpFullParams
from orxtra.tool._preview import check_and_preview
from orxtra.tool._renderers import JsonRenderer

_HTTP_DESCRIPTION = (
    "Make HTTP requests. Supports GET, POST, PUT, DELETE, PATCH,"
    " and HEAD methods. In consult mode, only GET and HEAD are"
    " available."
)


async def _execute_http_request(
    *,
    method: str,
    url: str,
    headers: dict[str, str] | None,
    body: str | None,
    requested_timeout: int | None,
    allowed_hosts: list[str] | str,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
) -> ToolOutput[HttpResponse]:
    """Shared HTTP execution logic for both full and consult mode tools."""
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

    result_dict: dict[str, Any] = {
        "status_code": response.status_code,
        "headers": dict(response.headers),
        "body": preview_result.content,
        "elapsed_ms": elapsed_ms,
    }

    return ToolOutput(
        data=HttpResponse(
            status_code=response.status_code,
            headers=dict(response.headers),
            body=response_body,
            elapsed_ms=elapsed_ms,
        ),
        text=json.dumps(result_dict),
    )


@tool("http", _HTTP_DESCRIPTION, renderer=JsonRenderer())
async def _http_full_impl(
    params: HttpFullParams,
    *,
    allowed_hosts: list[str] | str,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
) -> ToolOutput[HttpResponse]:
    return await _execute_http_request(
        method=params.method,
        url=params.url,
        headers=params.headers,
        body=params.body,
        requested_timeout=params.timeout,
        allowed_hosts=allowed_hosts,
        timeout_ceiling=timeout_ceiling,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
    )


@tool("http", _HTTP_DESCRIPTION, renderer=JsonRenderer())
async def _http_consult_impl(
    params: HttpConsultParams,
    *,
    allowed_hosts: list[str] | str,
    timeout_ceiling: int,
    preview_threshold: int,
    preview_lines: int,
) -> ToolOutput[HttpResponse]:
    return await _execute_http_request(
        method=params.method,
        url=params.url,
        headers=params.headers,
        body=params.body,
        requested_timeout=params.timeout,
        allowed_hosts=allowed_hosts,
        timeout_ceiling=timeout_ceiling,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
    )


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
    template = _http_consult_impl if consult_mode else _http_full_impl
    return template.bind(
        allowed_hosts=allowed_hosts,
        timeout_ceiling=timeout_ceiling,
        preview_threshold=preview_threshold,
        preview_lines=preview_lines,
    )
