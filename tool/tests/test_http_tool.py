"""Tests for the HTTP tool constructor."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from orxtra.protocols._tool import ToolError
from orxtra.tool._http_tool import make_http_tool


def _mock_response(
    status_code: int = 200,
    headers: dict[str, str] | None = None,
    text: str = "",
) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.headers = httpx.Headers(headers or {})
    resp.text = text
    return resp


def _mock_client(response: MagicMock) -> AsyncMock:
    mock = AsyncMock()
    mock.request = AsyncMock(return_value=response)
    mock.__aenter__ = AsyncMock(return_value=mock)
    mock.__aexit__ = AsyncMock(return_value=None)
    return mock


class TestMakeHttpTool:
    """Constructor-level tests for make_http_tool."""

    def test_tool_name_is_http(self) -> None:
        """Tool name is 'http'."""
        tool = make_http_tool("allow_all")
        assert tool.name == "http"

    def test_consult_mode_restricts_methods(self) -> None:
        """Schema enum is ['GET', 'HEAD'] in consult mode."""
        tool = make_http_tool("allow_all", consult_mode=True)
        method_enum = tool.parameters["properties"]["method"]["enum"]
        assert method_enum == ["GET", "HEAD"]


class TestGetRequest:
    """Tests for basic GET requests."""

    @pytest.mark.asyncio
    async def test_get_request_returns_status_headers_body(self) -> None:
        """Basic GET returns status_code, headers, body, and elapsed_ms."""
        tool = make_http_tool("allow_all")
        resp = _mock_response(
            status_code=200,
            headers={"content-type": "text/plain"},
            text="hello",
        )
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            raw = (await tool.execute({"method": "GET", "url": "http://example.com/"})).text
        result: dict[str, Any] = json.loads(raw)
        assert result["status_code"] == 200
        assert result["body"] == "hello"
        assert "content-type" in result["headers"]
        assert isinstance(result["elapsed_ms"], int)


class TestPostRequest:
    """Tests for POST requests."""

    @pytest.mark.asyncio
    async def test_post_with_body(self) -> None:
        """POST passes body to client.request as content."""
        tool = make_http_tool("allow_all")
        resp = _mock_response(text="created")
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            await tool.execute({
                "method": "POST",
                "url": "http://example.com/api",
                "body": '{"key": "value"}',
            })
        call_kwargs = mock.request.call_args[1]
        assert call_kwargs["content"] == '{"key": "value"}'


class TestHostAllowlist:
    """Tests for host allowlist enforcement."""

    @pytest.mark.asyncio
    async def test_allowed_host_succeeds(self) -> None:
        """Host in allowlist allows the request to proceed."""
        tool = make_http_tool(["example.com"])
        resp = _mock_response(text="ok")
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            raw = (await tool.execute({"method": "GET", "url": "http://example.com/"})).text
        result = json.loads(raw)
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_disallowed_host_raises_tool_error(self) -> None:
        """Host not in allowlist raises ToolError."""
        tool = make_http_tool(["safe.com"])
        with pytest.raises(ToolError, match="not in the allowed list"):
            await tool.execute({"method": "GET", "url": "http://evil.com/"})

    @pytest.mark.asyncio
    async def test_allow_all_permits_any_host(self) -> None:
        """'allow_all' string allows any host."""
        tool = make_http_tool("allow_all")
        resp = _mock_response(text="ok")
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            raw = (await tool.execute({"method": "GET", "url": "http://anything.xyz/"})).text
        result = json.loads(raw)
        assert result["status_code"] == 200


class TestTimeout:
    """Tests for timeout handling."""

    @pytest.mark.asyncio
    async def test_timeout_raises_tool_error(self) -> None:
        """httpx.TimeoutException becomes ToolError."""
        tool = make_http_tool("allow_all")
        mock = AsyncMock()
        mock.request = AsyncMock(side_effect=httpx.ReadTimeout("timed out"))
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock),
            pytest.raises(ToolError, match="timed out"),
        ):
            await tool.execute({"method": "GET", "url": "http://example.com/"})

    @pytest.mark.asyncio
    async def test_timeout_ceiling_enforced(self) -> None:
        """Requested timeout exceeding ceiling is capped to ceiling."""
        tool = make_http_tool(["example.com"], timeout_ceiling=10)
        resp = _mock_response(text="ok")
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            await tool.execute({
                "method": "GET",
                "url": "http://example.com/",
                "timeout": 999,
            })
        call_kwargs = mock.request.call_args[1]
        assert call_kwargs["timeout"] == 10


class TestErrorHandling:
    """Tests for error paths."""

    @pytest.mark.asyncio
    async def test_invalid_url_raises_tool_error(self) -> None:
        """URL with no extractable hostname raises ToolError."""
        tool = make_http_tool("allow_all")
        with pytest.raises(ToolError, match="Cannot extract hostname"):
            await tool.execute({"method": "GET", "url": "not-a-url"})

    @pytest.mark.asyncio
    async def test_request_error_raises_tool_error(self) -> None:
        """httpx.RequestError becomes ToolError."""
        tool = make_http_tool("allow_all")
        mock = AsyncMock()
        mock.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)
        with (
            patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock),
            pytest.raises(ToolError, match="Request failed"),
        ):
            await tool.execute({"method": "GET", "url": "http://example.com/"})


class TestPreview:
    """Tests for large response body preview."""

    @pytest.mark.asyncio
    async def test_large_response_body_is_previewed(self) -> None:
        """Body exceeding preview_threshold gets truncated/previewed."""
        full_body = "\n".join(f"line {i}" for i in range(200))
        tool = make_http_tool("allow_all", preview_threshold=10, preview_lines=3)
        resp = _mock_response(text=full_body)
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            raw = (await tool.execute({"method": "GET", "url": "http://example.com/"})).text
        result = json.loads(raw)
        assert result["body"] != full_body
        assert "omitted" in result["body"]


class TestConsultMode:
    """Tests for consult mode restrictions."""

    @pytest.mark.asyncio
    async def test_consult_mode_get_allowed(self) -> None:
        """GET works in consult mode."""
        tool = make_http_tool("allow_all", consult_mode=True)
        resp = _mock_response(text="ok")
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            raw = (await tool.execute({"method": "GET", "url": "http://example.com/"})).text
        result = json.loads(raw)
        assert result["status_code"] == 200

    @pytest.mark.asyncio
    async def test_consult_mode_post_rejected(self) -> None:
        """POST fails schema validation in consult mode."""
        tool = make_http_tool("allow_all", consult_mode=True)
        with pytest.raises(ToolError, match="Invalid tool arguments"):
            await tool.execute({"method": "POST", "url": "http://example.com/"})


class TestResponseHeaders:
    """Tests for response header capture."""

    @pytest.mark.asyncio
    async def test_response_headers_captured(self) -> None:
        """Custom response headers appear in result."""
        tool = make_http_tool("allow_all")
        resp = _mock_response(
            headers={"x-custom": "foobar", "x-request-id": "abc123"},
            text="ok",
        )
        mock = _mock_client(resp)
        with patch("orxtra.tool._http_tool.httpx.AsyncClient", return_value=mock):
            raw = (await tool.execute({"method": "GET", "url": "http://example.com/"})).text
        result = json.loads(raw)
        assert result["headers"]["x-custom"] == "foobar"
        assert result["headers"]["x-request-id"] == "abc123"
