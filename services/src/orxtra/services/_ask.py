from __future__ import annotations

import asyncio
import json
from typing import Any

from orxtra.transport import Result, Transport

from orxtra.services._providers import _DEFAULT_RETRY_POLICY, _PROVIDER_TYPES


def _build_transport(
    provider_type: str,
    api_key: str,
    *,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> Transport:
    """Construct a Transport from provider parameters."""
    if provider_type not in _PROVIDER_TYPES:
        msg = (
            f"Unknown provider_type {provider_type!r}."
            f" Supported: {', '.join(sorted(_PROVIDER_TYPES))}"
        )
        raise ValueError(msg)

    kwargs: dict[str, Any] = {"api_key": api_key}
    if base_url is not None:
        kwargs["base_url"] = base_url
    if provider_type == "anthropic":
        kwargs["max_tokens"] = max_tokens

    provider = _PROVIDER_TYPES[provider_type](**kwargs)
    return Transport(provider, _DEFAULT_RETRY_POLICY)


async def ask(  # noqa: PLR0913
    prompt: str,
    provider_type: str,
    model: str,
    api_key: str,
    *,
    system_prompt: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> str:
    """Send a prompt to an LLM and get text back.

    No PG, no scheduler, no workflows. A one-shot convenience function
    that constructs a provider, sends a single message, and returns the
    result text.
    """
    transport = _build_transport(
        provider_type, api_key, base_url=base_url, max_tokens=max_tokens,
    )
    stream = transport.send(
        prompt,
        model=model,
        system_prompt=system_prompt or "",
        tools=[],
    )
    async for event in stream:
        if isinstance(event, Result):
            return event.text

    msg = "No result received from LLM"
    raise RuntimeError(msg)


async def ask_structured(  # noqa: PLR0913
    prompt: str,
    provider_type: str,
    model: str,
    api_key: str,
    schema: dict[str, Any],
    *,
    system_prompt: str | None = None,
    base_url: str | None = None,
    max_tokens: int = 4096,
) -> dict[str, Any]:
    """Send a prompt and get a JSON-validated response.

    Wraps ``ask()`` with a system prompt instructing the LLM to respond
    with JSON matching the given schema, then parses and validates the
    response.
    """
    schema_json = json.dumps(schema, indent=2)
    structured_system = (
        "You must respond with valid JSON only. No markdown, no explanation,"
        " no code fences. Your response must conform to this JSON schema:\n\n"
        f"{schema_json}"
    )
    if system_prompt is not None:
        structured_system = f"{system_prompt}\n\n{structured_system}"

    raw = await ask(
        prompt,
        provider_type,
        model,
        api_key,
        system_prompt=structured_system,
        base_url=base_url,
        max_tokens=max_tokens,
    )

    try:
        result: dict[str, Any] = json.loads(raw)
    except json.JSONDecodeError as e:
        msg = f"LLM response is not valid JSON: {e}"
        raise ValueError(msg) from e

    if not isinstance(result, dict):
        msg = f"LLM response is not a JSON object, got {type(result).__name__}"
        raise ValueError(msg)

    # Validate required fields from schema
    required = schema.get("required", [])
    for field in required:
        if field not in result:
            msg = f"Response missing required field: {field!r}"
            raise ValueError(msg)

    return result


def sync_ask(
    prompt: str,
    provider_type: str,
    model: str,
    api_key: str,
    **kwargs: Any,
) -> str:
    """Synchronous wrapper around :func:`ask`.

    Constructs a provider, sends a single message, and returns
    the result text. Intended for scripts and synchronous consumers
    that cannot use async/await.
    """
    return asyncio.run(
        ask(prompt, provider_type, model, api_key, **kwargs),
    )
