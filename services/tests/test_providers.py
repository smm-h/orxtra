from __future__ import annotations

import pytest
from orxtra.services._providers import build_transport_registry
from orxtra.transport import Transport
from orxtra.transport.providers import AnthropicProvider, OpenAIProvider


class TestBuildTransportRegistry:
    def test_single_anthropic(self) -> None:
        configs = {
            "anthropic": {"type": "anthropic", "api_key": "sk-test"},
        }
        registry = build_transport_registry(configs)
        assert "anthropic" in registry
        assert isinstance(registry["anthropic"], Transport)
        # Verify the provider was constructed with the right key
        provider = registry["anthropic"]._provider
        assert isinstance(provider, AnthropicProvider)
        assert provider.api_key == "sk-test"

    def test_single_openai(self) -> None:
        configs = {
            "openai": {"type": "openai", "api_key": "sk-openai-test"},
        }
        registry = build_transport_registry(configs)
        assert "openai" in registry
        assert isinstance(registry["openai"], Transport)
        provider = registry["openai"]._provider
        assert isinstance(provider, OpenAIProvider)
        assert provider.api_key == "sk-openai-test"

    def test_multiple_providers(self) -> None:
        configs = {
            "anthropic": {"type": "anthropic", "api_key": "sk-a"},
            "openai": {"type": "openai", "api_key": "sk-o"},
        }
        registry = build_transport_registry(configs)
        assert len(registry) == 2
        assert isinstance(registry["anthropic"]._provider, AnthropicProvider)
        assert isinstance(registry["openai"]._provider, OpenAIProvider)

    def test_custom_base_url(self) -> None:
        configs = {
            "custom": {
                "type": "openai",
                "api_key": "sk-custom",
                "base_url": "https://custom.example.com/v1",
            },
        }
        registry = build_transport_registry(configs)
        provider = registry["custom"]._provider
        assert isinstance(provider, OpenAIProvider)
        assert provider.base_url == "https://custom.example.com/v1"

    def test_missing_type_raises(self) -> None:
        configs = {
            "bad": {"api_key": "sk-test"},
        }
        with pytest.raises(ValueError, match="missing required 'type' field"):
            build_transport_registry(configs)

    def test_unknown_type_raises(self) -> None:
        configs = {
            "bad": {"type": "gemini", "api_key": "sk-test"},
        }
        with pytest.raises(ValueError, match="Unknown provider type 'gemini'"):
            build_transport_registry(configs)

    def test_empty_configs(self) -> None:
        registry = build_transport_registry({})
        assert registry == {}

    def test_anthropic_custom_api_version(self) -> None:
        configs = {
            "anthropic": {
                "type": "anthropic",
                "api_key": "sk-test",
                "api_version": "2024-01-01",
            },
        }
        registry = build_transport_registry(configs)
        provider = registry["anthropic"]._provider
        assert isinstance(provider, AnthropicProvider)
        assert provider.api_version == "2024-01-01"

    def test_config_key_differs_from_type(self) -> None:
        """The registry key can be anything, not just the provider type."""
        configs = {
            "my-claude": {"type": "anthropic", "api_key": "sk-test"},
        }
        registry = build_transport_registry(configs)
        assert "my-claude" in registry
        assert isinstance(registry["my-claude"]._provider, AnthropicProvider)

    def test_does_not_mutate_input(self) -> None:
        configs = {
            "anthropic": {"type": "anthropic", "api_key": "sk-test"},
        }
        original = dict(configs["anthropic"])
        build_transport_registry(configs)
        assert configs["anthropic"] == original
