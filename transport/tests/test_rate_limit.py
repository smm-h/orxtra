from __future__ import annotations

from typing import Any

import httpx
import respx
from orxtra.transport._events import (
    ApiRetry,
    ContentBlock,
    Event,
    RateLimit,
    Result,
    Usage,
)
from orxtra.transport._provider import RetryPolicy
from orxtra.transport._transport import Transport

from .test_transport import MockProvider


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MOCK_URL = "https://mock.api/v1/messages"


def _retry_policy(
    max_retries: int = 3,
    base: float = 0.001,
    max_backoff: float = 0.01,
    *,
    jitter: bool = False,
) -> RetryPolicy:
    return RetryPolicy(
        max_retries=max_retries,
        backoff_base_seconds=base,
        backoff_max_seconds=max_backoff,
        jitter=jitter,
    )


async def _collect(
    transport: Transport, message: str, **kwargs: Any,  # noqa: ANN401
) -> list[Event]:
    return [event async for event in transport.send(message, **kwargs)]


def _default_send_kwargs(**overrides: Any) -> dict[str, Any]:  # noqa: ANN401
    defaults: dict[str, Any] = {
        "model": "test-model",
        "system_prompt": "sys",
        "tools": [],
    }
    defaults.update(overrides)
    return defaults


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestRateLimitHeaders:
    @respx.mock
    async def test_rate_limit_event_emitted_on_429(self) -> None:
        response_iter = iter([
            httpx.Response(
                429,
                text="rate limited",
                headers={
                    "retry-after": "2",
                    "x-ratelimit-limit-requests": "100",
                    "x-ratelimit-remaining-requests": "0",
                },
            ),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="Hello")],
                    Usage(input_tokens=5, output_tokens=3),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        rate_limits = [e for e in events if isinstance(e, RateLimit)]
        assert len(rate_limits) == 1
        rl = rate_limits[0]
        assert rl.resets_at == "2"
        assert rl.limit == 100
        assert rl.remaining == 0
        assert rl.utilization == 1.0

        # RateLimit should come before ApiRetry
        rl_idx = next(i for i, e in enumerate(events) if isinstance(e, RateLimit))
        retry_idx = next(i for i, e in enumerate(events) if isinstance(e, ApiRetry))
        assert rl_idx < retry_idx

        # Should still get a successful result
        results = [e for e in events if isinstance(e, Result)]
        assert len(results) == 1
        assert results[0].text == "Hello"

    @respx.mock
    async def test_rate_limit_with_token_headers(self) -> None:
        response_iter = iter([
            httpx.Response(
                429,
                text="rate limited",
                headers={
                    "x-ratelimit-limit-tokens": "1000000",
                    "x-ratelimit-remaining-tokens": "500000",
                },
            ),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        rate_limits = [e for e in events if isinstance(e, RateLimit)]
        assert len(rate_limits) == 1
        rl = rate_limits[0]
        assert rl.resets_at is None
        assert rl.limit == 1000000
        assert rl.remaining == 500000
        assert rl.utilization is not None
        assert abs(rl.utilization - 0.5) < 1e-9

    @respx.mock
    async def test_no_rate_limit_event_without_headers(self) -> None:
        response_iter = iter([
            httpx.Response(429, text="rate limited"),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        rate_limits = [e for e in events if isinstance(e, RateLimit)]
        assert len(rate_limits) == 0

        # Still get retry event
        retries = [e for e in events if isinstance(e, ApiRetry)]
        assert len(retries) == 1
        assert retries[0].status_code == 429

    @respx.mock
    async def test_no_rate_limit_on_500(self) -> None:
        """Rate limit events only emitted on 429, not other transient errors."""
        response_iter = iter([
            httpx.Response(500, text="internal error"),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        rate_limits = [e for e in events if isinstance(e, RateLimit)]
        assert len(rate_limits) == 0

    @respx.mock
    async def test_retry_after_only(self) -> None:
        response_iter = iter([
            httpx.Response(
                429,
                text="rate limited",
                headers={"retry-after": "5"},
            ),
            httpx.Response(200, json={"mock": True}),
        ])
        respx.post(_MOCK_URL).mock(side_effect=lambda _req: next(response_iter))

        provider = MockProvider(
            responses=[
                (
                    [ContentBlock(type="text", text="OK")],
                    Usage(),
                ),
            ],
        )
        transport = Transport(provider=provider, retry_policy=_retry_policy())
        events = await _collect(transport, "Hi", **_default_send_kwargs())

        rate_limits = [e for e in events if isinstance(e, RateLimit)]
        assert len(rate_limits) == 1
        rl = rate_limits[0]
        assert rl.resets_at == "5"
        assert rl.limit is None
        assert rl.remaining is None
        assert rl.utilization is None
