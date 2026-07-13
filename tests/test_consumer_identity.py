"""Cross-module integrity: consumers created mint-first are resolvable.

Proves the auth -> identity handoff the consumers vertical establishes: a
consumer created through the mint-first path (its principal minted with
kind=consumer, external_ref=consumer id, then the row persisted with both ids)
is found by ``resolve_caller_principal``. This closes the integrity gap the
resolver treats as a hard error -- every consumer now has a backing principal.

The PG counterpart lives in ``tests/test_auth_pg.py`` (docker-gated).
"""
from __future__ import annotations

from datetime import UTC, datetime

import uuid6
from orxtra.auth import InMemoryAuthBackend
from orxtra.identity import InMemoryPrincipalStorage, resolve_caller_principal
from orxtra.protocols import KIND_CONSUMER, AuthContext, TrustTier


async def test_create_consumer_mint_first_resolves_in_memory() -> None:
    auth = InMemoryAuthBackend()
    storage = InMemoryPrincipalStorage()

    name = "resolver-consumer"
    consumer_id = uuid6.uuid7()
    # Mint the consumer's own principal first, then persist the row with both
    # ids -- the mint-first pattern.
    principal = await storage.mint_principal(KIND_CONSUMER, consumer_id, name)
    returned = await auth.create_consumer(
        name,
        TrustTier.IDENTIFIED,
        ["events:read"],
        consumer_id=consumer_id,
        principal_id=principal.id,
    )
    assert returned == consumer_id

    # The consumer row carries the identity link.
    consumer = await auth.get_consumer(consumer_id)
    assert consumer is not None
    assert consumer.principal_id == principal.id

    # An auth context for this consumer resolves to its minted principal.
    ctx = AuthContext(
        id=uuid6.uuid7(),
        consumer_id=consumer_id,
        scopes=frozenset(),
        trust_tier=TrustTier.IDENTIFIED,
        authenticated_via="test",
        issued_at=datetime.now(UTC),
        expires_at=None,
    )
    resolved = await resolve_caller_principal(ctx, storage)
    assert resolved.id == principal.id
    assert resolved.kind == KIND_CONSUMER
    assert resolved.external_ref == consumer_id
