from __future__ import annotations

import os

from orxtra.secrets._registry import SecretRegistry


def create_secret_registry(
    env_mapping: dict[str, str],
) -> SecretRegistry:
    """Build a SecretRegistry by reading environment variables.

    *env_mapping* maps **secret names** to **environment variable names**.
    Each env var is read from ``os.environ``; a missing env var is a hard
    error (no implicit defaults).

    This is the single construction path for SecretRegistry in
    production, consumed by ``start_run``, the serve lifecycle, and the
    incoming webhook receiver.

    Raises ``KeyError`` when a required environment variable is not set.
    """
    missing: list[str] = []
    secrets: dict[str, str] = {}
    for secret_name, env_var in env_mapping.items():
        value = os.environ.get(env_var)
        if value is None:
            missing.append(env_var)
        else:
            secrets[secret_name] = value
    if missing:
        msg = (
            f"Missing environment variables for secrets: {sorted(missing)}"
        )
        raise KeyError(msg)
    return SecretRegistry(secrets)
