"""PG round-trip tests for AuthBackend.

Exercises AuthBackend against a real PostgreSQL database via
testcontainers. Skips gracefully when docker is unavailable.
"""
from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

import pytest

from orxtra.auth import AuthBackend
from orxtra.protocols import TrustTier

from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import asyncpg

pytestmark = skip_no_docker


class TestConsumerCRUD:
    """Consumer create, read, disable round-trips."""

    async def test_create_and_read_consumer(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "roundtrip-consumer",
            TrustTier.VERIFIED,
            ["events:read", "events:write"],
        )

        consumer = await backend.get_consumer(consumer_id)
        assert consumer is not None
        assert consumer.id == consumer_id
        assert consumer.name == "roundtrip-consumer"
        assert consumer.trust_tier == TrustTier.VERIFIED
        assert consumer.scope_grants == ["events:read", "events:write"]
        assert consumer.disabled_at is None
        assert consumer.created_at is not None

    async def test_disable_consumer(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "disable-me", TrustTier.IDENTIFIED, ["read"],
        )

        # Before disable: disabled_at is None.
        consumer = await backend.get_consumer(consumer_id)
        assert consumer is not None
        assert consumer.disabled_at is None

        await backend.disable_consumer(consumer_id)

        consumer = await backend.get_consumer(consumer_id)
        assert consumer is not None
        assert consumer.disabled_at is not None

    async def test_get_nonexistent_consumer(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        import uuid  # noqa: PLC0415

        backend = AuthBackend(pg_pool)
        result = await backend.get_consumer(uuid.uuid4())
        assert result is None


class TestCredentialCreation:
    """Credential create and lookup round-trips."""

    async def test_create_bearer_and_read_by_hash(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "bearer-consumer", TrustTier.VERIFIED, [],
        )

        raw_value = "my-bearer-token-secret"
        cred_id = await backend.create_credential(
            consumer_id, "bearer", raw_value,
        )

        expected_hash = hashlib.sha256(raw_value.encode()).hexdigest()
        cred = await backend.get_credential_by_hash(expected_hash)
        assert cred is not None
        assert cred.id == cred_id
        assert cred.consumer_id == consumer_id
        assert cred.credential_type == "bearer"
        assert cred.credential_hash == expected_hash
        assert cred.algorithm == "sha256"
        assert cred.secret_ref is None
        assert cred.created_at is not None

    async def test_create_credential_and_read_by_id(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "api-key-consumer", TrustTier.IDENTIFIED, ["admin"],
        )

        cred_id = await backend.create_credential(
            consumer_id, "api_key", "secret-api-key-123",
        )

        cred = await backend.get_credential_by_id(cred_id)
        assert cred is not None
        assert cred.id == cred_id
        assert cred.consumer_id == consumer_id
        assert cred.credential_type == "api_key"
        assert cred.secret_ref is None


class TestHmacCredential:
    """HMAC credential with secret_ref preservation."""

    async def test_hmac_secret_ref_preserved(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "hmac-consumer", TrustTier.VERIFIED, [],
        )

        cred_id = await backend.create_credential(
            consumer_id,
            "hmac",
            "hmac-identifier-value",
            secret_ref="webhook_secret",
        )

        cred = await backend.get_credential_by_id(cred_id)
        assert cred is not None
        assert cred.credential_type == "hmac"
        assert cred.secret_ref == "webhook_secret"
        assert cred.consumer_id == consumer_id


class TestCascadeDelete:
    """FK cascade: deleting a consumer removes its credentials.

    AuthBackend has no delete_consumer method, so we use raw SQL
    to verify the schema's ON DELETE CASCADE constraint.
    """

    async def test_consumer_delete_cascades_to_credentials(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "cascade-consumer", TrustTier.VERIFIED, [],
        )
        cred_id_1 = await backend.create_credential(
            consumer_id, "bearer", "token-one",
        )
        cred_id_2 = await backend.create_credential(
            consumer_id, "api_key", "key-one",
        )

        # Verify credentials exist before deletion.
        assert await backend.get_credential_by_id(cred_id_1) is not None
        assert await backend.get_credential_by_id(cred_id_2) is not None

        # Delete the consumer via raw SQL (no backend method for this).
        async with pg_pool.acquire() as conn:
            await conn.execute(
                "DELETE FROM consumers WHERE id = $1", consumer_id,
            )

        # Both credentials should be gone due to ON DELETE CASCADE.
        assert await backend.get_credential_by_id(cred_id_1) is None
        assert await backend.get_credential_by_id(cred_id_2) is None

        # Consumer itself is gone.
        assert await backend.get_consumer(consumer_id) is None


class TestGetCredentialsByConsumer:
    """get_credentials_by_consumer queries."""

    async def test_multiple_credentials_returned(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "multi-cred-consumer", TrustTier.VERIFIED, [],
        )

        await backend.create_credential(consumer_id, "bearer", "tok1")
        await backend.create_credential(consumer_id, "api_key", "key1")
        await backend.create_credential(consumer_id, "bearer", "tok2")

        all_creds = await backend.get_credentials_by_consumer(consumer_id)
        assert len(all_creds) == 3

        # Filter by type.
        bearer_creds = await backend.get_credentials_by_consumer(
            consumer_id, credential_type="bearer",
        )
        assert len(bearer_creds) == 2
        assert all(c.credential_type == "bearer" for c in bearer_creds)

        api_key_creds = await backend.get_credentials_by_consumer(
            consumer_id, credential_type="api_key",
        )
        assert len(api_key_creds) == 1
        assert api_key_creds[0].credential_type == "api_key"

    async def test_no_credentials_returns_empty(
        self, pg_pool: asyncpg.Pool
    ) -> None:
        backend = AuthBackend(pg_pool)
        consumer_id = await backend.create_consumer(
            "no-creds-consumer", TrustTier.ANONYMOUS, [],
        )

        creds = await backend.get_credentials_by_consumer(consumer_id)
        assert creds == []
