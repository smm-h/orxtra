from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from orxtra.transport import Usage


@dataclass(frozen=True)
class TokenRates:
    input_per_million: Decimal
    output_per_million: Decimal
    cache_read_per_million: Decimal
    cache_write_per_million: Decimal
    reasoning_per_million: Decimal


PRICING_TABLE: dict[str, TokenRates] = {
    "anthropic/claude-opus-4-6": TokenRates(
        input_per_million=Decimal("5.00"),
        output_per_million=Decimal("25.00"),
        cache_read_per_million=Decimal("0.50"),
        cache_write_per_million=Decimal("6.25"),
        reasoning_per_million=Decimal("60.00"),
    ),
    "anthropic/claude-sonnet-4-6": TokenRates(
        input_per_million=Decimal("3.00"),
        output_per_million=Decimal("15.00"),
        cache_read_per_million=Decimal("0.30"),
        cache_write_per_million=Decimal("3.75"),
        reasoning_per_million=Decimal("20.00"),
    ),
    "anthropic/claude-haiku-4-5": TokenRates(
        input_per_million=Decimal("1.00"),
        output_per_million=Decimal("5.00"),
        cache_read_per_million=Decimal("0.10"),
        cache_write_per_million=Decimal("1.25"),
        reasoning_per_million=Decimal("2.00"),
    ),
    "openai/gpt-4o": TokenRates(
        input_per_million=Decimal("2.50"),
        output_per_million=Decimal("10.00"),
        cache_read_per_million=Decimal("1.25"),
        cache_write_per_million=Decimal("2.50"),
        reasoning_per_million=Decimal("10.00"),
    ),
    "openai/gpt-4o-mini": TokenRates(
        input_per_million=Decimal("0.15"),
        output_per_million=Decimal("0.60"),
        cache_read_per_million=Decimal("0.075"),
        cache_write_per_million=Decimal("0.15"),
        reasoning_per_million=Decimal("0.60"),
    ),
    "google/gemini-2.5-flash": TokenRates(
        input_per_million=Decimal("0.30"),
        output_per_million=Decimal("2.50"),
        cache_read_per_million=Decimal("0.03"),
        cache_write_per_million=Decimal("0.30"),
        reasoning_per_million=Decimal("2.50"),
    ),
}

_MILLION = Decimal(1000000)


def compute_cost_usd(model: str, usage: Usage) -> Decimal:
    if model not in PRICING_TABLE:
        msg = f"Unknown model: {model!r}"
        raise ValueError(msg)
    rates = PRICING_TABLE[model]
    return (
        rates.input_per_million * usage.input_tokens
        + rates.output_per_million * usage.output_tokens
        + rates.reasoning_per_million * usage.reasoning_tokens
        + rates.cache_read_per_million * usage.cache_read_tokens
        + rates.cache_write_per_million * usage.cache_write_tokens
    ) / _MILLION
