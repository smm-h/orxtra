from __future__ import annotations

from orxtra.transport import RetryPolicy, Transport
from orxtra.transport.providers import AnthropicProvider, OpenAIProvider

_PROVIDER_TYPES = {
    "anthropic": AnthropicProvider,
    "openai": OpenAIProvider,
}

_DEFAULT_RETRY_POLICY = RetryPolicy(
    max_retries=3,
    backoff_base_seconds=1.0,
    backoff_max_seconds=30.0,
    jitter=True,
)


def build_transport_registry(
    provider_configs: dict[str, dict[str, str]],
) -> dict[str, Transport]:
    """Build a transport registry from provider configuration.

    provider_configs format::

        {
            "anthropic": {"api_key": "sk-...", "type": "anthropic"},
            "openai": {"api_key": "sk-...", "type": "openai"},
        }

    The ``type`` field selects the provider class (anthropic/openai).
    All other fields are passed as keyword arguments to the provider
    constructor.  The registry is keyed by the config key (e.g.
    ``"anthropic"``).
    """
    registry: dict[str, Transport] = {}
    for name, config in provider_configs.items():
        config = dict(config)  # shallow copy so we can pop
        provider_type = config.pop("type", None)
        if provider_type is None:
            msg = (
                f"Provider config '{name}' missing required 'type' field"
            )
            raise ValueError(msg)
        provider_cls = _PROVIDER_TYPES.get(provider_type)
        if provider_cls is None:
            msg = (
                f"Unknown provider type '{provider_type}' in config '{name}'."
                f" Supported: {', '.join(sorted(_PROVIDER_TYPES))}"
            )
            raise ValueError(msg)
        provider = provider_cls(**config)
        registry[name] = Transport(provider, _DEFAULT_RETRY_POLICY)
    return registry
