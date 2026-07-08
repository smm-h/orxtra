"""Tests for the http execution type factory (data-defined tools).

Covers:
- Secret substitution at call time (never in Tool description/params).
- Output schema validation (missing field = ToolError, match = success).
- Parameter interpolation (URL-encoding, pattern validation).
- Effect tag derivation (GET/HEAD = readonly, else = mutation).
- Error handling (missing required args, unexpected args, pattern mismatch).
"""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from orxtra.protocols import ToolError
from orxtra.secrets import SecretRegistry
from orxtra.tool._data_tool_http import build_http_tool
from orxtra.tool._data_tool_types import (
    DataToolDefinition,
    HttpExecution,
    OutputConfig,
    ParamDef,
)

_HTTPX_CLIENT = "orxtra.tool._data_tool_http.httpx.AsyncClient"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _make_definition(
    *,
    name: str = "test_tool",
    description: str = "A test tool",
    namespace: str = "custom.test",
    method: str = "GET",
    url: str = "https://api.example.com/v1/data",
    headers: dict[str, str] | None = None,
    body_template: str | None = None,
    params: dict[str, ParamDef] | None = None,
    output_schema: dict[str, Any] | None = None,
    deferred: bool = False,
    tags: list[str] | None = None,
) -> DataToolDefinition:
    """Build a DataToolDefinition for testing."""
    execution = HttpExecution(
        type="http",
        method=method,
        url=url,
        headers=headers,
        body_template=body_template,
    )
    output = (
        OutputConfig(schema_=output_schema) if output_schema else None
    )
    return DataToolDefinition(
        name=name,
        description=description,
        namespace=namespace,
        deferred=deferred,
        tags=tags,
        params=params or {},
        execution=execution,
        output=output,
    )


# ---------------------------------------------------------------------------
# Secret substitution
# ---------------------------------------------------------------------------


class TestSecretSubstitution:
    """Secrets are substituted at call time; never in Tool metadata."""

    @pytest.mark.asyncio
    async def test_secret_in_header_substituted_at_call_time(
        self,
    ) -> None:
        """{{secret:TOKEN}} in headers is replaced with the real value
        in the outgoing request, but NEVER appears in Tool.description
        or Tool.parameters."""
        registry = SecretRegistry({"TOKEN": "real-secret-abc123"})
        defn = _make_definition(
            method="GET",
            url="https://api.example.com/data",
            headers={
                "Authorization": "Bearer {{secret:TOKEN}}",
            },
        )
        tool = build_http_tool(defn, secret_registry=registry)

        # Tool metadata must NOT contain the real secret value.
        assert "real-secret-abc123" not in tool.description
        params_json = json.dumps(tool.parameters)
        assert "real-secret-abc123" not in params_json
        # The placeholder should also not leak into parameters.
        assert "{{secret:TOKEN}}" not in params_json

        # Execute and verify the real value reaches the request.
        resp = _mock_response(text='{"ok": true}')
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            await tool.execute({})

        call_kwargs = mock.request.call_args[1]
        assert (
            call_kwargs["headers"]["Authorization"]
            == "Bearer real-secret-abc123"
        )

    @pytest.mark.asyncio
    async def test_secret_in_url_substituted_at_call_time(
        self,
    ) -> None:
        """{{secret:API_KEY}} in URL is replaced at call time."""
        registry = SecretRegistry({"API_KEY": "key-xyz"})
        defn = _make_definition(
            method="GET",
            url="https://api.example.com/data?key={{secret:API_KEY}}",
        )
        tool = build_http_tool(defn, secret_registry=registry)

        # Tool description must not contain the secret.
        assert "key-xyz" not in tool.description

        resp = _mock_response(text='{"ok": true}')
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            await tool.execute({})

        call_kwargs = mock.request.call_args[1]
        assert "key-xyz" in call_kwargs["url"]

    @pytest.mark.asyncio
    async def test_secret_in_body_template_substituted(
        self,
    ) -> None:
        """{{secret:TOKEN}} in body_template is replaced at call time."""
        registry = SecretRegistry({"TOKEN": "secret-body-val"})
        defn = _make_definition(
            method="POST",
            url="https://api.example.com/data",
            body_template='{"auth": "{{secret:TOKEN}}"}',
        )
        tool = build_http_tool(defn, secret_registry=registry)

        resp = _mock_response(text='{"ok": true}')
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            await tool.execute({})

        call_kwargs = mock.request.call_args[1]
        assert "secret-body-val" in call_kwargs["content"]

    @pytest.mark.asyncio
    async def test_secret_placeholder_without_registry_hard_error(
        self,
    ) -> None:
        """{{secret:...}} with no registry raises ToolError."""
        defn = _make_definition(
            method="GET",
            url="https://api.example.com/data",
            headers={
                "Authorization": "Bearer {{secret:TOKEN}}",
            },
        )
        # No registry provided.
        tool = build_http_tool(defn, secret_registry=None)

        with pytest.raises(
            ToolError, match="SecretRegistry was provided",
        ):
            await tool.execute({})


# ---------------------------------------------------------------------------
# Output schema validation
# ---------------------------------------------------------------------------


class TestOutputSchemaValidation:
    """Output schema enforced as hard ToolError on mismatch."""

    @pytest.mark.asyncio
    async def test_missing_declared_output_field_is_tool_error(
        self,
    ) -> None:
        """Response missing a required output field raises ToolError."""
        defn = _make_definition(
            output_schema={
                "type": "object",
                "properties": {
                    "temperature": {"type": "number"},
                    "humidity": {"type": "number"},
                },
                "required": ["temperature", "humidity"],
            },
        )
        tool = build_http_tool(defn)

        # Response only has temperature, missing humidity.
        resp = _mock_response(text='{"temperature": 22.5}')
        mock = _mock_client(resp)
        with (
            patch(_HTTPX_CLIENT, return_value=mock),
            pytest.raises(ToolError, match="humidity"),
        ):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_matching_output_schema_succeeds(self) -> None:
        """Response matching the output schema returns validated data."""
        defn = _make_definition(
            output_schema={
                "type": "object",
                "properties": {
                    "temperature": {"type": "number"},
                    "city": {"type": "string"},
                },
                "required": ["temperature", "city"],
            },
        )
        tool = build_http_tool(defn)

        resp = _mock_response(
            text='{"temperature": 22.5, "city": "Berlin"}',
        )
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            result = await tool.execute({})

        assert result.data["temperature"] == 22.5
        assert result.data["city"] == "Berlin"

    @pytest.mark.asyncio
    async def test_wrong_type_in_output_is_tool_error(self) -> None:
        """Response with wrong type for a field raises ToolError."""
        defn = _make_definition(
            output_schema={
                "type": "object",
                "properties": {
                    "count": {"type": "integer"},
                },
                "required": ["count"],
            },
        )
        tool = build_http_tool(defn)

        # count is a string, not an integer.
        resp = _mock_response(text='{"count": "not-a-number"}')
        mock = _mock_client(resp)
        with (
            patch(_HTTPX_CLIENT, return_value=mock),
            pytest.raises(
                ToolError, match="Output validation failed",
            ),
        ):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_no_output_schema_skips_validation(self) -> None:
        """No output schema: any response is accepted."""
        defn = _make_definition(output_schema=None)
        tool = build_http_tool(defn)

        resp = _mock_response(text="arbitrary non-json text")
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            result = await tool.execute({})

        assert result.data == "arbitrary non-json text"


# ---------------------------------------------------------------------------
# Parameter interpolation
# ---------------------------------------------------------------------------


class TestParameterInterpolation:
    """URL parameter interpolation with URL-encoding and patterns."""

    @pytest.mark.asyncio
    async def test_param_interpolated_and_url_encoded(self) -> None:
        """{ticket_id} in URL is replaced and URL-encoded."""
        defn = _make_definition(
            url="https://api.example.com/tickets/{ticket_id}",
            params={
                "ticket_id": ParamDef(
                    type="string",
                    description="The ticket ID",
                    required=True,
                ),
            },
        )
        tool = build_http_tool(defn)

        resp = _mock_response(text='{"id": "ABC 123"}')
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            await tool.execute({"ticket_id": "ABC 123"})

        call_kwargs = mock.request.call_args[1]
        # Space should be encoded as %20.
        assert "ABC%20123" in call_kwargs["url"]
        assert "ABC 123" not in call_kwargs["url"]

    @pytest.mark.asyncio
    async def test_param_with_pattern_mismatch_is_tool_error(
        self,
    ) -> None:
        """Param value not matching its pattern raises ToolError."""
        defn = _make_definition(
            url="https://api.example.com/users/{user_id}",
            params={
                "user_id": ParamDef(
                    type="string",
                    description="User ID (alphanumeric only)",
                    required=True,
                    pattern="^[A-Za-z0-9]+$",
                ),
            },
        )
        tool = build_http_tool(defn)

        with pytest.raises(ToolError, match="does not match pattern"):
            await tool.execute({"user_id": "invalid user!"})

    @pytest.mark.asyncio
    async def test_param_with_pattern_match_succeeds(self) -> None:
        """Param value matching its pattern is accepted."""
        defn = _make_definition(
            url="https://api.example.com/users/{user_id}",
            params={
                "user_id": ParamDef(
                    type="string",
                    description="User ID",
                    required=True,
                    pattern="^[A-Za-z0-9]+$",
                ),
            },
        )
        tool = build_http_tool(defn)

        resp = _mock_response(text='{"name": "Alice"}')
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            result = await tool.execute({"user_id": "Alice42"})

        assert result.data["name"] == "Alice"

    @pytest.mark.asyncio
    async def test_missing_url_param_is_tool_error(self) -> None:
        """URL placeholder without matching arg raises ToolError."""
        defn = _make_definition(
            url="https://api.example.com/items/{item_id}",
            params={
                "item_id": ParamDef(
                    type="string",
                    description="The item ID",
                    required=True,
                ),
            },
        )
        tool = build_http_tool(defn)

        with pytest.raises(
            ToolError, match="Missing required argument",
        ):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_unexpected_arg_is_tool_error(self) -> None:
        """Arg not declared in params raises ToolError."""
        defn = _make_definition(
            url="https://api.example.com/data",
            params={},
        )
        tool = build_http_tool(defn)

        with pytest.raises(ToolError, match="Unexpected arguments"):
            await tool.execute({"unknown_param": "value"})


# ---------------------------------------------------------------------------
# Effect tags
# ---------------------------------------------------------------------------


class TestEffectTags:
    """Effect tags derived from HTTP method."""

    def test_get_tool_has_readonly_tag(self) -> None:
        """GET-only tool carries the readonly tag."""
        defn = _make_definition(method="GET")
        tool = build_http_tool(defn)
        assert "readonly" in tool.tags
        assert "mutation" not in tool.tags

    def test_head_tool_has_readonly_tag(self) -> None:
        """HEAD tool carries the readonly tag."""
        defn = _make_definition(method="HEAD")
        tool = build_http_tool(defn)
        assert "readonly" in tool.tags
        assert "mutation" not in tool.tags

    def test_post_tool_has_mutation_tag(self) -> None:
        """POST tool carries the mutation tag."""
        defn = _make_definition(method="POST")
        tool = build_http_tool(defn)
        assert "mutation" in tool.tags
        assert "readonly" not in tool.tags

    def test_put_tool_has_mutation_tag(self) -> None:
        """PUT tool carries the mutation tag."""
        defn = _make_definition(method="PUT")
        tool = build_http_tool(defn)
        assert "mutation" in tool.tags
        assert "readonly" not in tool.tags

    def test_delete_tool_has_mutation_tag(self) -> None:
        """DELETE tool carries the mutation tag."""
        defn = _make_definition(method="DELETE")
        tool = build_http_tool(defn)
        assert "mutation" in tool.tags
        assert "readonly" not in tool.tags

    def test_patch_tool_has_mutation_tag(self) -> None:
        """PATCH tool carries the mutation tag."""
        defn = _make_definition(method="PATCH")
        tool = build_http_tool(defn)
        assert "mutation" in tool.tags
        assert "readonly" not in tool.tags


# ---------------------------------------------------------------------------
# Tool metadata
# ---------------------------------------------------------------------------


class TestToolMetadata:
    """Tool attributes match the definition."""

    def test_name_matches_definition(self) -> None:
        defn = _make_definition(name="my_api_tool")
        tool = build_http_tool(defn)
        assert tool.name == "my_api_tool"

    def test_description_matches_definition(self) -> None:
        defn = _make_definition(
            description="Fetches data from My API",
        )
        tool = build_http_tool(defn)
        assert tool.description == "Fetches data from My API"

    def test_namespace_matches_definition(self) -> None:
        defn = _make_definition(namespace="custom.myapi")
        tool = build_http_tool(defn)
        assert tool.namespace == "custom.myapi"

    def test_deferred_matches_definition(self) -> None:
        defn = _make_definition(deferred=True)
        tool = build_http_tool(defn)
        assert tool.deferred is True

    def test_parameters_schema_has_correct_structure(self) -> None:
        defn = _make_definition(
            params={
                "query": ParamDef(
                    type="string",
                    description="Search query",
                    required=True,
                    pattern="^.+$",
                ),
                "limit": ParamDef(
                    type="integer",
                    description="Max results",
                    required=False,
                ),
            },
        )
        tool = build_http_tool(defn)
        props = tool.parameters["properties"]
        assert tool.parameters["type"] == "object"
        assert "query" in props
        assert "limit" in props
        assert tool.parameters["required"] == ["query"]
        assert props["query"]["type"] == "string"
        assert props["query"]["pattern"] == "^.+$"
        assert props["limit"]["type"] == "integer"


# ---------------------------------------------------------------------------
# HTTP error handling
# ---------------------------------------------------------------------------


class TestHttpErrors:
    """HTTP-level errors become ToolError."""

    @pytest.mark.asyncio
    async def test_timeout_raises_tool_error(self) -> None:
        defn = _make_definition()
        tool = build_http_tool(defn, timeout_ceiling=5)

        mock = AsyncMock()
        mock.request = AsyncMock(
            side_effect=httpx.ReadTimeout("timed out"),
        )
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(_HTTPX_CLIENT, return_value=mock),
            pytest.raises(ToolError, match="timed out"),
        ):
            await tool.execute({})

    @pytest.mark.asyncio
    async def test_connection_error_raises_tool_error(self) -> None:
        defn = _make_definition()
        tool = build_http_tool(defn)

        mock = AsyncMock()
        mock.request = AsyncMock(
            side_effect=httpx.ConnectError("connection refused"),
        )
        mock.__aenter__ = AsyncMock(return_value=mock)
        mock.__aexit__ = AsyncMock(return_value=None)

        with (
            patch(_HTTPX_CLIENT, return_value=mock),
            pytest.raises(ToolError, match="Request failed"),
        ):
            await tool.execute({})


# ---------------------------------------------------------------------------
# Body template parameter interpolation
# ---------------------------------------------------------------------------


class TestBodyTemplateInterpolation:
    """Parameters are interpolated in body_template."""

    @pytest.mark.asyncio
    async def test_param_in_body_template_substituted(self) -> None:
        """Parameters in body_template are replaced with values."""
        defn = _make_definition(
            method="POST",
            url="https://api.example.com/data",
            body_template=(
                '{"name": "{user_name}", "age": {user_age}}'
            ),
            params={
                "user_name": ParamDef(
                    type="string",
                    description="User name",
                    required=True,
                ),
                "user_age": ParamDef(
                    type="integer",
                    description="User age",
                    required=True,
                ),
            },
        )
        tool = build_http_tool(defn)

        resp = _mock_response(text='{"ok": true}')
        mock = _mock_client(resp)
        with patch(_HTTPX_CLIENT, return_value=mock):
            await tool.execute(
                {"user_name": "Alice", "user_age": 30},
            )

        call_kwargs = mock.request.call_args[1]
        body = call_kwargs["content"]
        assert '"Alice"' in body
        assert "30" in body


# ---------------------------------------------------------------------------
# Non-HttpExecution rejection
# ---------------------------------------------------------------------------


class TestNonHttpExecution:
    """build_http_tool rejects non-HttpExecution definitions."""

    def test_wrong_execution_type_raises_type_error(self) -> None:
        from orxtra.tool._data_tool_types import (
            MontyExecution,
            ResourceLimits,
        )

        defn = DataToolDefinition(
            name="test",
            description="test",
            namespace="custom.test",
            deferred=False,
            tags=None,
            params={},
            execution=MontyExecution(
                type="monty",
                code="x = 1",
                capabilities=[],
                limits=ResourceLimits(max_duration_secs=30),
            ),
            output=None,
        )

        with pytest.raises(TypeError, match="Expected HttpExecution"):
            build_http_tool(defn)
