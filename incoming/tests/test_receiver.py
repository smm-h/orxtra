"""Tests for the incoming webhook receiver.

Covers: GitHub-style signed payload, wrong signature, unknown slug,
NULL-credential source, unmapped payload, oversized body, idempotency
key extraction, bearer/api_key auth, and compositor mounting.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
from fastware import Router, create_app
from fastware.testing import AsyncTestClient

from orxtra.auth import (
    Authenticator,
    HmacCredentialVerifier,
    InMemoryAuthBackend,
)
from orxtra.auth._verifiers import HashCredentialVerifier
from orxtra.dispatch._memory_backend import InMemoryDispatchBackend
from orxtra.incoming._receiver import create_incoming_router
from orxtra.protocols import Source, TrustTier
from orxtra.secrets import SecretRegistry
from orxtra.secrets._mac_provider import EnvMacProvider

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

WEBHOOK_SECRET = "test-webhook-secret"
SLUG = "github"
CONSUMER_NAME = "github-webhook"
CREDENTIAL_ID: UUID | None = None  # Set during fixture setup.


def _sign_payload(body: bytes, secret: str, algorithm: str = "sha256") -> str:
    """Compute HMAC signature for a payload."""
    return hmac.new(
        secret.encode(),
        body,
        getattr(hashlib, algorithm),
    ).hexdigest()


@pytest.fixture
def dispatch_backend() -> InMemoryDispatchBackend:
    return InMemoryDispatchBackend()


@pytest.fixture
def auth_backend() -> InMemoryAuthBackend:
    return InMemoryAuthBackend()


@pytest.fixture
def secret_registry() -> SecretRegistry:
    return SecretRegistry({"github_webhook_secret": WEBHOOK_SECRET})


@pytest.fixture
def mac_provider(secret_registry: SecretRegistry) -> EnvMacProvider:
    return EnvMacProvider(secret_registry)


@pytest.fixture
def authenticator(
    auth_backend: InMemoryAuthBackend,
    mac_provider: EnvMacProvider,
) -> Authenticator:
    verifiers: dict[str, Any] = {
        "hmac": HmacCredentialVerifier(mac_provider, auth_backend),
        "api_key": HashCredentialVerifier("api_key", auth_backend),
        "bearer": HashCredentialVerifier("bearer", auth_backend),
    }
    return Authenticator(auth_backend, verifiers)


@pytest.fixture
async def source_with_hmac(
    dispatch_backend: InMemoryDispatchBackend,
    auth_backend: InMemoryAuthBackend,
) -> Source:
    """Create a source with HMAC credential configured."""
    # Create consumer and credential.
    consumer_id = await auth_backend.create_consumer(
        CONSUMER_NAME, TrustTier.IDENTIFIED, ["events:write"],
    )
    cred_id = await auth_backend.create_credential(
        consumer_id,
        "hmac",
        "github-hmac-identifier",
        secret_ref="github_webhook_secret",
    )

    now = datetime.now(tz=UTC)
    source = Source(
        id=UUID("00000000-0000-0000-0000-000000000001"),
        slug=SLUG,
        name="GitHub",
        credential_id=cred_id,
        config={
            "event_type_source": "header",
            "event_type_field": "X-GitHub-Event",
            "signature_header": "X-Hub-Signature-256",
            "idempotency_header": "X-GitHub-Delivery",
        },
        created_at=now,
    )
    await dispatch_backend.create_source(source)
    return source


@pytest.fixture
async def source_with_bearer(
    dispatch_backend: InMemoryDispatchBackend,
    auth_backend: InMemoryAuthBackend,
) -> Source:
    """Create a source with bearer credential configured."""
    consumer_id = await auth_backend.create_consumer(
        "bearer-source", TrustTier.IDENTIFIED, ["events:write"],
    )
    cred_id = await auth_backend.create_credential(
        consumer_id,
        "bearer",
        "my-bearer-token-123",
    )

    now = datetime.now(tz=UTC)
    source = Source(
        id=UUID("00000000-0000-0000-0000-000000000002"),
        slug="stripe",
        name="Stripe",
        credential_id=cred_id,
        config={
            "event_type_source": "json_field",
            "event_type_field": "type",
        },
        created_at=now,
    )
    await dispatch_backend.create_source(source)
    return source


@pytest.fixture
async def source_no_credential(
    dispatch_backend: InMemoryDispatchBackend,
) -> Source:
    """Create a source with no credential (NULL credential_id)."""
    now = datetime.now(tz=UTC)
    source = Source(
        id=UUID("00000000-0000-0000-0000-000000000003"),
        slug="unauthenticated",
        name="Unauthenticated Source",
        credential_id=None,
        config={
            "event_type_source": "constant",
            "event_type_field": "generic.event",
        },
        created_at=now,
    )
    await dispatch_backend.create_source(source)
    return source


@pytest.fixture
async def source_no_mapping(
    dispatch_backend: InMemoryDispatchBackend,
    auth_backend: InMemoryAuthBackend,
) -> Source:
    """Create a source with credential but no event_type mapping config."""
    consumer_id = await auth_backend.create_consumer(
        "no-mapping-consumer", TrustTier.IDENTIFIED, ["events:write"],
    )
    cred_id = await auth_backend.create_credential(
        consumer_id,
        "bearer",
        "no-mapping-token",
    )

    now = datetime.now(tz=UTC)
    source = Source(
        id=UUID("00000000-0000-0000-0000-000000000004"),
        slug="unmapped",
        name="Unmapped Source",
        credential_id=cred_id,
        config={},  # No event_type_source or event_type_field.
        created_at=now,
    )
    await dispatch_backend.create_source(source)
    return source


def _make_app(
    dispatch_backend: InMemoryDispatchBackend,
    authenticator: Authenticator,
    max_body_bytes: int = 1_048_576,
) -> Any:  # noqa: ANN401
    """Build a minimal ASGI app with the incoming router."""
    # We use a mock pool since fire_event is mocked in tests.
    mock_pool = AsyncMock()
    incoming_router = create_incoming_router(
        pool=mock_pool,
        dispatch_backend=dispatch_backend,
        authenticator=authenticator,
        max_body_bytes=max_body_bytes,
    )
    root = Router()
    root.include_router(incoming_router, prefix="/incoming")
    return create_app(root)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestGitHubStyleHmacPayload:
    """GitHub-style signed payload -> 202, event stored."""

    async def test_valid_signature_returns_202(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"action": "opened"}).encode()
        signature = _sign_payload(body, WEBHOOK_SECRET)

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("11111111-1111-1111-1111-111111111111"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    f"/incoming/events/{SLUG}",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "X-GitHub-Event": "push",
                        "X-Hub-Signature-256": f"sha256={signature}",
                        "X-GitHub-Delivery": "delivery-123",
                    },
                )

            assert resp.status_code == 202
            data = resp.json()
            assert data["event_id"] == "11111111-1111-1111-1111-111111111111"
            assert data["inserted"] is True

            # Verify fire_event was called with correct args.
            mock_fire.assert_called_once()
            call_args = mock_fire.call_args
            assert call_args[0][1] is None  # run_id
            assert call_args[0][2] == "push"  # event_type
            assert call_args[0][3] == {"action": "opened"}  # data
            assert call_args[1]["source"] == SLUG
            assert call_args[1]["idempotency_key"] == "delivery-123"


class TestWrongSignature:
    """Wrong HMAC signature -> 401."""

    async def test_wrong_signature_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"action": "opened"}).encode()
        wrong_sig = "deadbeef" * 8

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                f"/incoming/events/{SLUG}",
                content=body,
                headers={
                    "content-type": "application/json",
                    "X-GitHub-Event": "push",
                    "X-Hub-Signature-256": f"sha256={wrong_sig}",
                },
            )

        assert resp.status_code == 401


class TestUnknownSlug:
    """Unknown slug -> 404."""

    async def test_unknown_slug_returns_404(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"test": True}).encode()

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                "/incoming/events/nonexistent",
                content=body,
                headers={"content-type": "application/json"},
            )

        assert resp.status_code == 404


class TestNullCredentialSource:
    """Source with NULL credential_id -> 403."""

    async def test_null_credential_returns_403(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_no_credential: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"test": True}).encode()

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                "/incoming/events/unauthenticated",
                content=body,
                headers={"content-type": "application/json"},
            )

        assert resp.status_code == 403


class TestUnmappedPayload:
    """Source with no event_type_source in config -> 400."""

    async def test_missing_mapping_returns_400(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_no_mapping: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"test": True}).encode()

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                "/incoming/events/unmapped",
                content=body,
                headers={
                    "content-type": "application/json",
                    "Authorization": "Bearer no-mapping-token",
                },
            )

        assert resp.status_code == 400


class TestOversizedBody:
    """Oversized body -> 413."""

    async def test_oversized_body_returns_413(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        # Set a very small max body size.
        app = _make_app(dispatch_backend, authenticator, max_body_bytes=100)
        body = b"x" * 200

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                f"/incoming/events/{SLUG}",
                content=body,
                headers={"content-type": "application/json"},
            )

        assert resp.status_code == 413


class TestIdempotencyKey:
    """Idempotency key extracted from configured header and passed through."""

    async def test_idempotency_key_passed_to_fire_event(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"data": "test"}).encode()
        signature = _sign_payload(body, WEBHOOK_SECRET)

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("22222222-2222-2222-2222-222222222222"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    f"/incoming/events/{SLUG}",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "X-GitHub-Event": "issues",
                        "X-Hub-Signature-256": f"sha256={signature}",
                        "X-GitHub-Delivery": "unique-delivery-id-456",
                    },
                )

            assert resp.status_code == 202
            mock_fire.assert_called_once()
            assert mock_fire.call_args[1]["idempotency_key"] == "unique-delivery-id-456"

    async def test_no_idempotency_header_passes_none(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        """When the idempotency header is absent, None is passed."""
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"data": "test"}).encode()
        signature = _sign_payload(body, WEBHOOK_SECRET)

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("33333333-3333-3333-3333-333333333333"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    f"/incoming/events/{SLUG}",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "X-GitHub-Event": "issues",
                        "X-Hub-Signature-256": f"sha256={signature}",
                        # No X-GitHub-Delivery header.
                    },
                )

            assert resp.status_code == 202
            mock_fire.assert_called_once()
            assert mock_fire.call_args[1]["idempotency_key"] is None


class TestBearerAuth:
    """Bearer/api_key credential verification via Authorization header."""

    async def test_valid_bearer_returns_202(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"type": "payment_intent.succeeded", "data": {}}).encode()

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("44444444-4444-4444-4444-444444444444"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    "/incoming/events/stripe",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "Authorization": "Bearer my-bearer-token-123",
                    },
                )

            assert resp.status_code == 202
            mock_fire.assert_called_once()
            assert mock_fire.call_args[0][2] == "payment_intent.succeeded"

    async def test_wrong_bearer_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"type": "payment_intent.succeeded", "data": {}}).encode()

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                "/incoming/events/stripe",
                content=body,
                headers={
                    "content-type": "application/json",
                    "Authorization": "Bearer wrong-token",
                },
            )

        assert resp.status_code == 401


class TestEventTypeExtraction:
    """Event type extraction from various sources."""

    async def test_event_type_from_header(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"data": True}).encode()
        signature = _sign_payload(body, WEBHOOK_SECRET)

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("55555555-5555-5555-5555-555555555555"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    f"/incoming/events/{SLUG}",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "X-GitHub-Event": "pull_request",
                        "X-Hub-Signature-256": f"sha256={signature}",
                    },
                )

            assert resp.status_code == 202
            assert mock_fire.call_args[0][2] == "pull_request"

    async def test_event_type_from_json_field(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"type": "invoice.paid", "data": {}}).encode()

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("66666666-6666-6666-6666-666666666666"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    "/incoming/events/stripe",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "Authorization": "Bearer my-bearer-token-123",
                    },
                )

            assert resp.status_code == 202
            assert mock_fire.call_args[0][2] == "invoice.paid"

    async def test_event_type_constant(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        auth_backend: InMemoryAuthBackend,
    ) -> None:
        """Source with event_type_source=constant uses the field value directly."""
        consumer_id = await auth_backend.create_consumer(
            "constant-source", TrustTier.IDENTIFIED, ["events:write"],
        )
        cred_id = await auth_backend.create_credential(
            consumer_id, "bearer", "constant-token",
        )
        now = datetime.now(tz=UTC)
        source = Source(
            id=UUID("00000000-0000-0000-0000-000000000099"),
            slug="constant-src",
            name="Constant Source",
            credential_id=cred_id,
            config={
                "event_type_source": "constant",
                "event_type_field": "webhook.received",
            },
            created_at=now,
        )
        await dispatch_backend.create_source(source)

        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"some": "data"}).encode()

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("77777777-7777-7777-7777-777777777777"), True),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    "/incoming/events/constant-src",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "Authorization": "Bearer constant-token",
                    },
                )

            assert resp.status_code == 202
            assert mock_fire.call_args[0][2] == "webhook.received"

    async def test_missing_event_type_header_returns_400(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        """When the configured event type header is absent, return 400."""
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"data": True}).encode()
        signature = _sign_payload(body, WEBHOOK_SECRET)

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                f"/incoming/events/{SLUG}",
                content=body,
                headers={
                    "content-type": "application/json",
                    # Missing X-GitHub-Event header.
                    "X-Hub-Signature-256": f"sha256={signature}",
                },
            )

        assert resp.status_code == 400


class TestMissingSignatureHeader:
    """Missing HMAC signature header -> 401."""

    async def test_missing_signature_header_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"data": True}).encode()

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                f"/incoming/events/{SLUG}",
                content=body,
                headers={
                    "content-type": "application/json",
                    "X-GitHub-Event": "push",
                    # Missing X-Hub-Signature-256 header.
                },
            )

        assert resp.status_code == 401


class TestInvalidJsonBody:
    """Non-JSON body after successful auth -> 400."""

    async def test_non_json_body_returns_400(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        auth_backend: InMemoryAuthBackend,
    ) -> None:
        """Bearer-authenticated source with non-JSON body."""
        consumer_id = await auth_backend.create_consumer(
            "raw-source", TrustTier.IDENTIFIED, ["events:write"],
        )
        cred_id = await auth_backend.create_credential(
            consumer_id, "bearer", "raw-token",
        )
        now = datetime.now(tz=UTC)
        source = Source(
            id=UUID("00000000-0000-0000-0000-000000000098"),
            slug="raw-src",
            name="Raw Source",
            credential_id=cred_id,
            config={
                "event_type_source": "constant",
                "event_type_field": "raw.event",
            },
            created_at=now,
        )
        await dispatch_backend.create_source(source)

        app = _make_app(dispatch_backend, authenticator)
        body = b"this is not json"

        async with AsyncTestClient(app) as client:
            resp = await client.post(
                "/incoming/events/raw-src",
                content=body,
                headers={
                    "content-type": "application/json",
                    "Authorization": "Bearer raw-token",
                },
            )

        assert resp.status_code == 400


class TestDuplicateDelivery:
    """Duplicate delivery with idempotency key returns 202 but inserted=False."""

    async def test_duplicate_returns_202_not_inserted(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_hmac: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        body = json.dumps({"data": "test"}).encode()
        signature = _sign_payload(body, WEBHOOK_SECRET)

        with patch(
            "orxtra.incoming._receiver.fire_event",
            new_callable=AsyncMock,
            return_value=(UUID("88888888-8888-8888-8888-888888888888"), False),
        ) as mock_fire:
            async with AsyncTestClient(app) as client:
                resp = await client.post(
                    f"/incoming/events/{SLUG}",
                    content=body,
                    headers={
                        "content-type": "application/json",
                        "X-GitHub-Event": "push",
                        "X-Hub-Signature-256": f"sha256={signature}",
                        "X-GitHub-Delivery": "dup-key",
                    },
                )

            assert resp.status_code == 202
            data = resp.json()
            assert data["inserted"] is False
            mock_fire.assert_called_once()
