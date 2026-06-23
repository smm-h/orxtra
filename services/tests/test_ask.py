from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from orxtra.services._ask import ask, ask_structured
from orxtra.transport import Result, StepFinish, StepStart


async def _mock_send_events(
    message: str,
    *,
    model: str,
    system_prompt: str,
    tools: list[Any],
    session_id: str | None = None,
) -> Any:
    """Async generator that yields a minimal event sequence."""
    yield StepStart(session_id="test-session")
    yield StepFinish(reason="end_turn", input_tokens=10, output_tokens=20)
    yield Result(
        text="Hello from the LLM",
        session_id="test-session",
        total_input_tokens=10,
        total_output_tokens=20,
    )


async def _mock_send_json(
    message: str,
    *,
    model: str,
    system_prompt: str,
    tools: list[Any],
    session_id: str | None = None,
) -> Any:
    """Async generator that yields a JSON response."""
    response = json.dumps({"name": "Alice", "age": 30})
    yield StepStart(session_id="test-session")
    yield StepFinish(reason="end_turn")
    yield Result(text=response, session_id="test-session")


async def _mock_send_invalid_json(
    message: str,
    *,
    model: str,
    system_prompt: str,
    tools: list[Any],
    session_id: str | None = None,
) -> Any:
    """Async generator that yields non-JSON text."""
    yield StepStart(session_id="test-session")
    yield StepFinish(reason="end_turn")
    yield Result(text="This is not JSON", session_id="test-session")


async def _mock_send_json_array(
    message: str,
    *,
    model: str,
    system_prompt: str,
    tools: list[Any],
    session_id: str | None = None,
) -> Any:
    """Async generator that yields a JSON array (not object)."""
    yield StepStart(session_id="test-session")
    yield StepFinish(reason="end_turn")
    yield Result(text="[1, 2, 3]", session_id="test-session")


async def _mock_send_no_result(
    message: str,
    *,
    model: str,
    system_prompt: str,
    tools: list[Any],
    session_id: str | None = None,
) -> Any:
    """Async generator that yields no Result event."""
    yield StepStart(session_id="test-session")
    yield StepFinish(reason="end_turn")


class TestAsk:
    @pytest.mark.anyio
    async def test_returns_text(self) -> None:
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_events
            mock_build.return_value = transport

            result = await ask(
                "Hello",
                provider_type="anthropic",
                model="claude-sonnet-4-20250514",
                api_key="sk-test",
            )
            assert result == "Hello from the LLM"

    @pytest.mark.anyio
    async def test_with_system_prompt(self) -> None:
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_events
            mock_build.return_value = transport

            result = await ask(
                "Hello",
                provider_type="anthropic",
                model="claude-sonnet-4-20250514",
                api_key="sk-test",
                system_prompt="You are helpful.",
            )
            assert result == "Hello from the LLM"

    @pytest.mark.anyio
    async def test_no_result_raises(self) -> None:
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_no_result
            mock_build.return_value = transport

            with pytest.raises(RuntimeError, match="No result received"):
                await ask(
                    "Hello",
                    provider_type="anthropic",
                    model="test-model",
                    api_key="sk-test",
                )

    def test_unknown_provider_type_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown provider_type 'gemini'"):
            from orxtra.services._ask import _build_transport

            _build_transport("gemini", "sk-test")


class TestAskStructured:
    @pytest.mark.anyio
    async def test_valid_json_response(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "required": ["name", "age"],
            "properties": {
                "name": {"type": "string"},
                "age": {"type": "integer"},
            },
        }
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_json
            mock_build.return_value = transport

            result = await ask_structured(
                "Give me a person",
                provider_type="anthropic",
                model="test-model",
                api_key="sk-test",
                schema=schema,
            )
            assert result == {"name": "Alice", "age": 30}

    @pytest.mark.anyio
    async def test_invalid_json_raises(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_invalid_json
            mock_build.return_value = transport

            with pytest.raises(ValueError, match="not valid JSON"):
                await ask_structured(
                    "Give me a person",
                    provider_type="anthropic",
                    model="test-model",
                    api_key="sk-test",
                    schema=schema,
                )

    @pytest.mark.anyio
    async def test_json_array_raises(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"items": {"type": "array"}},
        }
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_json_array
            mock_build.return_value = transport

            with pytest.raises(ValueError, match="not a JSON object"):
                await ask_structured(
                    "Give me items",
                    provider_type="anthropic",
                    model="test-model",
                    api_key="sk-test",
                    schema=schema,
                )

    @pytest.mark.anyio
    async def test_missing_required_field_raises(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "required": ["name", "email"],
            "properties": {
                "name": {"type": "string"},
                "email": {"type": "string"},
            },
        }
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_json  # returns {"name": "Alice", "age": 30}
            mock_build.return_value = transport

            with pytest.raises(ValueError, match="missing required field: 'email'"):
                await ask_structured(
                    "Give me a person",
                    provider_type="anthropic",
                    model="test-model",
                    api_key="sk-test",
                    schema=schema,
                )

    @pytest.mark.anyio
    async def test_with_system_prompt(self) -> None:
        schema: dict[str, Any] = {
            "type": "object",
            "properties": {"name": {"type": "string"}},
        }
        with patch(
            "orxtra.services._ask._build_transport",
        ) as mock_build:
            transport = AsyncMock()
            transport.send = _mock_send_json
            mock_build.return_value = transport

            result = await ask_structured(
                "Give me a person",
                provider_type="anthropic",
                model="test-model",
                api_key="sk-test",
                schema=schema,
                system_prompt="Be concise.",
            )
            assert isinstance(result, dict)
            assert "name" in result
