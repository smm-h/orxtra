"""Env-mapping adapter implementing KeyedMacProvider.

Uses SecretRegistry.resolve() to get the raw key value, performs
hmac.compare_digest internally, and returns a MacVerdict. The raw
key value never leaves this module -- callers only see the verdict.

Supports multiple concurrent key versions for rotation: key_ref
values with a `:N` suffix (e.g., "webhook_secret:1", "webhook_secret:2")
are tried in order. The base name (without suffix) is also tried.
The first match wins and the verdict reports the matched version.
"""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime

from orxtra.protocols import MacOutcome, MacVerdict

from orxtra.secrets._registry import SecretRegistry


# Supported HMAC algorithms and their hashlib constructors.
_ALGORITHMS: dict[str, str] = {
    "sha256": "sha256",
    "hmac-sha256": "sha256",
    "sha1": "sha1",
    "hmac-sha1": "sha1",
    "sha512": "sha512",
    "hmac-sha512": "sha512",
}

# Maximum number of key versions to check during rotation.
_MAX_VERSIONS = 10


class EnvMacProvider:
    """KeyedMacProvider backed by a SecretRegistry.

    Performs HMAC verification internally -- the only operation is
    verify(). There is no get_value or resolve method, so key export
    is impossible by construction.

    Key rotation: if key_ref is "webhook_secret", the provider tries
    "webhook_secret", "webhook_secret:1", "webhook_secret:2", ...
    up to _MAX_VERSIONS. The first version whose HMAC matches wins.
    """

    def __init__(self, registry: SecretRegistry) -> None:
        self._registry = registry

    async def verify(
        self,
        key_ref: str,
        message: bytes,
        signature: str,
        algorithm: str,
    ) -> MacVerdict:
        algo = _ALGORITHMS.get(algorithm.lower())
        if algo is None:
            msg = (
                f"Unsupported HMAC algorithm {algorithm!r}; "
                f"supported: {sorted(_ALGORITHMS)}"
            )
            raise ValueError(msg)

        now = datetime.now(tz=UTC)

        # Try all key versions. Base name first (version=None), then
        # versioned names (key_ref:1, key_ref:2, ...).
        candidates = _build_candidates(key_ref)
        for secret_name, version in candidates:
            try:
                key_value = self._registry.resolve(secret_name)
            except KeyError:
                continue

            expected = hmac.new(
                key_value.encode(),
                message,
                getattr(hashlib, algo),
            ).hexdigest()

            if hmac.compare_digest(expected, signature):
                return MacVerdict(
                    outcome=MacOutcome.MATCH,
                    secret_name=key_ref,
                    algorithm=algorithm,
                    verified_at=now,
                    matched_version=version,
                )

        # No version matched.
        return MacVerdict(
            outcome=MacOutcome.MISMATCH,
            secret_name=key_ref,
            algorithm=algorithm,
            verified_at=now,
            matched_version=None,
        )


def _build_candidates(key_ref: str) -> list[tuple[str, int | None]]:
    """Build the list of (secret_name, version) candidates to try."""
    candidates: list[tuple[str, int | None]] = [(key_ref, None)]
    for i in range(1, _MAX_VERSIONS + 1):
        candidates.append((f"{key_ref}:{i}", i))
    return candidates
