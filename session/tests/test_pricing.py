from __future__ import annotations

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest
from orxtra.session._pricing import PRICING_TABLE, TokenRates, compute_cost_usd
from orxtra.transport import Usage


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


class TestExtraRates:
    def test_extra_rates_used_for_custom_model(self) -> None:
        # A model absent from PRICING_TABLE priced via config-provided rates.
        assert "openai/m" not in PRICING_TABLE
        extra = {
            "openai/m": TokenRates(
                input_per_million=Decimal("0"),
                output_per_million=Decimal("0"),
                cache_read_per_million=Decimal("0"),
                cache_write_per_million=Decimal("0"),
                reasoning_per_million=Decimal("0"),
            ),
        }
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = compute_cost_usd("openai/m", usage, extra_rates=extra)
        assert cost == Decimal(0)

    def test_extra_rates_nonzero_custom_model(self) -> None:
        extra = {
            "openai/gpt-oss-120b": TokenRates(
                input_per_million=Decimal("1.00"),
                output_per_million=Decimal("2.00"),
                cache_read_per_million=Decimal("0"),
                cache_write_per_million=Decimal("0"),
                reasoning_per_million=Decimal("0"),
            ),
        }
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = compute_cost_usd(
            "openai/gpt-oss-120b", usage, extra_rates=extra,
        )
        assert cost == Decimal("1.00") + Decimal("2.00")

    def test_unknown_model_still_raises_with_extra_rates(self) -> None:
        # A model in neither config nor the built-in table is a hard error,
        # even when other extra rates are supplied. No silent zero default.
        extra = {
            "openai/m": TokenRates(
                input_per_million=Decimal("0"),
                output_per_million=Decimal("0"),
                cache_read_per_million=Decimal("0"),
                cache_write_per_million=Decimal("0"),
                reasoning_per_million=Decimal("0"),
            ),
        }
        with pytest.raises(ValueError, match="Unknown model"):
            compute_cost_usd(
                "openai/totally-unknown",
                Usage(input_tokens=100),
                extra_rates=extra,
            )

    def test_extra_rates_override_builtin(self) -> None:
        # Config rates take precedence over the built-in table when both
        # define the same model.
        model = "anthropic/claude-sonnet-4-6"
        assert model in PRICING_TABLE
        extra = {
            model: TokenRates(
                input_per_million=Decimal("0.01"),
                output_per_million=Decimal("0.02"),
                cache_read_per_million=Decimal("0"),
                cache_write_per_million=Decimal("0"),
                reasoning_per_million=Decimal("0"),
            ),
        }
        usage = Usage(input_tokens=1_000_000, output_tokens=1_000_000)
        cost = compute_cost_usd(model, usage, extra_rates=extra)
        assert cost == Decimal("0.01") + Decimal("0.02")
        # And without extra_rates the built-in rate applies (unchanged).
        builtin = compute_cost_usd(model, usage)
        assert builtin == Decimal("3.00") + Decimal("15.00")


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
