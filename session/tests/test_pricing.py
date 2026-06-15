from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from orxt.session._pricing import PRICING_TABLE, TokenRates, compute_cost_usd
from orxt.transport import Usage


class TestComputeCostUsd:
    def test_known_model_returns_correct_cost(self) -> None:
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = compute_cost_usd("anthropic/claude-sonnet-4-6", usage)
        expected = Decimal("3.00") + Decimal("15.00")
        assert cost == expected

    def test_zero_usage_returns_zero(self) -> None:
        cost = compute_cost_usd("anthropic/claude-sonnet-4-6", Usage())
        assert cost == Decimal(0)

    def test_unknown_model_raises(self) -> None:
        with pytest.raises(ValueError, match="Unknown model"):
            compute_cost_usd("unknown/model", Usage(input_tokens=100))

    def test_cache_tokens_priced_correctly(self) -> None:
        usage = Usage(
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        cost = compute_cost_usd("anthropic/claude-sonnet-4-6", usage)
        expected = Decimal("0.30") + Decimal("3.75")
        assert cost == expected

    def test_cost_is_deterministic(self) -> None:
        usage = Usage(
            input_tokens=12345,
            output_tokens=67890,
            cache_read_tokens=11111,
            cache_write_tokens=22222,
        )
        results = {
            compute_cost_usd("anthropic/claude-sonnet-4-6", usage)
            for _ in range(10)
        }
        assert len(results) == 1

    def test_large_token_counts(self) -> None:
        usage = Usage(
            input_tokens=100_000_000,
            output_tokens=100_000_000,
        )
        cost = compute_cost_usd("anthropic/claude-opus-4-6", usage)
        expected = Decimal("5.00") * 100 + Decimal("25.00") * 100
        assert cost == expected

    def test_reasoning_tokens_priced_correctly(self) -> None:
        usage = Usage(reasoning_tokens=1_000_000)
        cost = compute_cost_usd("anthropic/claude-sonnet-4-6", usage)
        expected = Decimal("20.00")
        assert cost == expected

    def test_all_token_types_combined(self) -> None:
        usage = Usage(
            input_tokens=1_000_000,
            output_tokens=1_000_000,
            reasoning_tokens=1_000_000,
            cache_read_tokens=1_000_000,
            cache_write_tokens=1_000_000,
        )
        cost = compute_cost_usd("anthropic/claude-sonnet-4-6", usage)
        expected = (
            Decimal("3.00") + Decimal("15.00") + Decimal("20.00")
            + Decimal("0.30") + Decimal("3.75")
        )
        assert cost == expected


class TestPricingTable:
    def test_all_models_have_rates(self) -> None:
        for model, rates in PRICING_TABLE.items():
            assert rates.input_per_million is not None, (
                f"{model} missing input rate"
            )
            assert rates.output_per_million is not None, (
                f"{model} missing output rate"
            )
            assert rates.cache_read_per_million is not None, (
                f"{model} missing cache_read rate"
            )
            assert rates.cache_write_per_million is not None, (
                f"{model} missing cache_write rate"
            )
            assert rates.reasoning_per_million is not None, (
                f"{model} missing reasoning rate"
            )


class TestTokenRates:
    def test_frozen(self) -> None:
        rates = TokenRates(
            input_per_million=Decimal(1),
            output_per_million=Decimal(2),
            cache_read_per_million=Decimal(3),
            cache_write_per_million=Decimal(4),
            reasoning_per_million=Decimal(5),
        )
        with pytest.raises(FrozenInstanceError):
            rates.input_per_million = Decimal(999)  # type: ignore[misc]
