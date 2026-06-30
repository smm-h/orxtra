from __future__ import annotations

import json

import pytest

from orxtra.auth import (
    AuthenticationError,
    AuthorizationError,
    Authenticator,
    Authorizer,
    InMemoryAuthBackend,
    auth_middleware,
)
from orxtra.protocols import Principal, TrustTier


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> InMemoryAuthBackend:
    return InMemoryAuthBackend()


@pytest.fixture
def authenticator(backend: InMemoryAuthBackend) -> Authenticator:
    return Authenticator(backend)


@pytest.fixture
def authorizer() -> Authorizer:
    return Authorizer()


# ---------------------------------------------------------------------------
# Backend: create consumer + credential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_create_consumer_and_credential(
    backend: InMemoryAuthBackend,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "test-consumer", TrustTier.VERIFIED, ["read", "write"],
    )
    consumer = await backend.get_consumer(None, consumer_id)
    assert consumer is not None
    assert consumer.name == "test-consumer"
    assert consumer.trust_tier == TrustTier.VERIFIED
    assert consumer.scope_grants == ["read", "write"]
    assert consumer.disabled_at is None

    cred_id = await backend.create_credential(
        None, consumer_id, "api_key", "my-secret-key",
    )
    assert cred_id is not None

    # Credential is stored hashed, not raw
    creds = backend._get_credentials()
    cred = creds[cred_id]
    assert cred.credential_hash != "my-secret-key"
    assert cred.consumer_id == consumer_id
    assert cred.credential_type == "api_key"
    assert cred.algorithm == "sha256"


# ---------------------------------------------------------------------------
# Authenticator: correct key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_correct_key(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "api-user", TrustTier.IDENTIFIED, ["read"],
    )
    await backend.create_credential(
        None, consumer_id, "api_key", "valid-key-123",
    )

    principal = await authenticator.authenticate("valid-key-123")
    assert isinstance(principal, Principal)
    assert principal.consumer_id == consumer_id
    assert principal.trust_tier == TrustTier.IDENTIFIED
    assert "read" in principal.scopes
    assert principal.authenticated_via == "api_key"


# ---------------------------------------------------------------------------
# Authenticator: wrong key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_wrong_key(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "api-user", TrustTier.IDENTIFIED, ["read"],
    )
    await backend.create_credential(
        None, consumer_id, "api_key", "valid-key-123",
    )

    with pytest.raises(AuthenticationError, match="Invalid credential"):
        await authenticator.authenticate("wrong-key-456")


# ---------------------------------------------------------------------------
# Authorizer: allowed scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_allowed_scope(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
    authorizer: Authorizer,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "writer", TrustTier.VERIFIED, ["read", "write"],
    )
    await backend.create_credential(
        None, consumer_id, "bearer", "token-abc",
    )

    principal = await authenticator.authenticate("token-abc")
    # Should not raise
    authorizer.authorize(principal, "write")


# ---------------------------------------------------------------------------
# Authorizer: disallowed scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_disallowed_scope(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
    authorizer: Authorizer,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "reader", TrustTier.IDENTIFIED, ["read"],
    )
    await backend.create_credential(
        None, consumer_id, "api_key", "reader-key",
    )

    principal = await authenticator.authenticate("reader-key")
    with pytest.raises(AuthorizationError, match="lacks required scope"):
        authorizer.authorize(principal, "admin")


# ---------------------------------------------------------------------------
# Authenticator: disabled consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_disabled_consumer(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "disabled-user", TrustTier.VERIFIED, ["read"],
    )
    await backend.create_credential(
        None, consumer_id, "api_key", "disabled-key",
    )

    # Disable the consumer
    await backend.disable_consumer(None, consumer_id)

    with pytest.raises(AuthenticationError, match="disabled"):
        await authenticator.authenticate("disabled-key")


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------


async def _echo_app(
    scope: dict,  # noqa: ANN401
    receive: object,
    send: object,
) -> None:
    """Simple ASGI app that returns 200 with principal info."""
    principal = scope.get("state", {}).get("principal")
    body = json.dumps({
        "authenticated": principal is not None,
        "consumer_id": str(principal.consumer_id) if principal else None,
    }).encode()

    await send({  # type: ignore[operator]
        "type": "http.response.start",
        "status": 200,
        "headers": [
            [b"content-type", b"application/json"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({  # type: ignore[operator]
        "type": "http.response.body",
        "body": body,
    })


class _ResponseCapture:
    """Captures ASGI send() calls for assertion."""

    def __init__(self) -> None:
        self.status: int | None = None
        self.headers: list[tuple[bytes, bytes]] = []
        self.body: bytes = b""

    async def __call__(self, message: dict) -> None:  # noqa: ANN401
        if message["type"] == "http.response.start":
            self.status = message["status"]
            self.headers = message.get("headers", [])
        elif message["type"] == "http.response.body":
            self.body += message.get("body", b"")


@pytest.mark.asyncio
async def test_middleware_missing_auth_header(
    authenticator: Authenticator,
) -> None:
    app = auth_middleware(_echo_app, authenticator)
    scope = {"type": "http", "headers": []}
    capture = _ResponseCapture()

    await app(scope, None, capture)
    assert capture.status == 401
    data = json.loads(capture.body)
    assert "Missing" in data["error"]


@pytest.mark.asyncio
async def test_middleware_invalid_credential(
    authenticator: Authenticator,
) -> None:
    app = auth_middleware(_echo_app, authenticator)
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer bad-token")],
    }
    capture = _ResponseCapture()

    await app(scope, None, capture)
    assert capture.status == 401
    data = json.loads(capture.body)
    assert "Invalid" in data["error"]


@pytest.mark.asyncio
async def test_middleware_valid_credential(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await backend.create_consumer(
        None, "mw-user", TrustTier.VERIFIED, ["api"],
    )
    await backend.create_credential(
        None, consumer_id, "bearer", "valid-mw-token",
    )

    app = auth_middleware(_echo_app, authenticator)
    scope = {
        "type": "http",
        "headers": [(b"authorization", b"Bearer valid-mw-token")],
    }
    capture = _ResponseCapture()

    await app(scope, None, capture)
    assert capture.status == 200
    data = json.loads(capture.body)
    assert data["authenticated"] is True
    assert data["consumer_id"] == str(consumer_id)


@pytest.mark.asyncio
async def test_middleware_non_http_passthrough(
    authenticator: Authenticator,
) -> None:
    """Non-HTTP scopes (websocket, lifespan) pass through without auth."""
    called = False

    async def passthrough_app(
        scope: dict,  # noqa: ANN401
        receive: object,
        send: object,
    ) -> None:
        nonlocal called
        called = True

    app = auth_middleware(passthrough_app, authenticator)
    scope = {"type": "websocket", "headers": []}

    await app(scope, None, None)
    assert called
