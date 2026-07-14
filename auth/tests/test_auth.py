from __future__ import annotations

import hashlib
import hmac
import json
from uuid import UUID

import pytest
from orxtra.auth import (
    AuthAuditEvent,
    AuthenticationError,
    Authenticator,
    AuthorizationError,
    Authorizer,
    HashCredentialVerifier,
    HmacCredentialVerifier,
    InMemoryAuthBackend,
    auth_middleware,
)
from orxtra.protocols import (
    ALL_SCOPES,
    SCOPE_CONFIG_READ,
    SCOPE_EVENTS_READ,
    SCOPE_EVENTS_WRITE,
    SCOPE_INBOX_READ,
    SCOPE_INBOX_RESPOND,
    SCOPE_NOTIFICATIONS_MANAGE,
    SCOPE_NOTIFICATIONS_READ,
    SCOPE_PRINCIPALS_MANAGE,
    SCOPE_PRINCIPALS_READ,
    SCOPE_RUNS_MANAGE,
    SCOPE_RUNS_READ,
    SCOPE_SOURCES_MANAGE,
    SCOPE_SOURCES_READ,
    SCOPE_SUBSCRIPTIONS_MANAGE,
    SCOPE_SUBSCRIPTIONS_READ,
    SCOPE_TRACE_READ,
    SCOPE_VALIDATE_READ,
    AuthContext,
    KeyedMacProvider,
    MacOutcome,
    TrustTier,
)
from orxtra.secrets import EnvMacProvider, SecretRegistry
from uuid6 import uuid7

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def backend() -> InMemoryAuthBackend:
    return InMemoryAuthBackend()


@pytest.fixture
def authenticator(backend: InMemoryAuthBackend) -> Authenticator:
    verifiers = {
        "api_key": HashCredentialVerifier("api_key", backend),
        "bearer": HashCredentialVerifier("bearer", backend),
    }
    return Authenticator(backend, verifiers)


@pytest.fixture
def authorizer() -> Authorizer:
    return Authorizer()


# ---------------------------------------------------------------------------
# Audit sink for testing
# ---------------------------------------------------------------------------


class CollectingSink:
    """EventSink that collects all events for assertion."""

    def __init__(self) -> None:
        self.events: list[AuthAuditEvent] = []

    async def on_event(self, event: AuthAuditEvent) -> None:
        self.events.append(event)


# ---------------------------------------------------------------------------
# Backend: create consumer + credential (pool-free API)
# ---------------------------------------------------------------------------


async def _mk_consumer(
    backend: InMemoryAuthBackend,
    name: str,
    trust_tier: TrustTier,
    scope_grants: list[str],
) -> UUID:
    """Register a consumer via the mint-first flow (throwaway ids for in-memory).

    In-memory has no principals FK, so the consumer/principal ids are stand-ins;
    the resolver-integrity flow is exercised separately in the identity tests.
    """
    return await backend.create_consumer(
        name,
        trust_tier,
        scope_grants,
        consumer_id=uuid7(),
        principal_id=uuid7(),
    )


@pytest.mark.asyncio
async def test_create_consumer_and_credential(
    backend: InMemoryAuthBackend,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "test-consumer", TrustTier.VERIFIED, ["read", "write"],
    )
    consumer = await backend.get_consumer(consumer_id)
    assert consumer is not None
    assert consumer.name == "test-consumer"
    assert consumer.trust_tier == TrustTier.VERIFIED
    assert consumer.scope_grants == ["read", "write"]
    assert consumer.disabled_at is None

    cred_id = await backend.create_credential(
        consumer_id, "api_key", "my-secret-key",
    )
    assert cred_id is not None

    # Credential is stored hashed, not raw
    creds = backend._get_credentials()
    cred = creds[cred_id]
    assert cred.credential_hash != "my-secret-key"
    assert cred.consumer_id == consumer_id
    assert cred.credential_type == "api_key"
    assert cred.algorithm == "sha256"
    assert cred.secret_ref is None


# ---------------------------------------------------------------------------
# Backend: secret_ref stored on credential
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_credential_stores_secret_ref(
    backend: InMemoryAuthBackend,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "hmac-consumer", TrustTier.VERIFIED, [],
    )
    cred_id = await backend.create_credential(
        consumer_id,
        "hmac",
        "hmac-identifier",
        secret_ref="webhook_secret",
    )
    creds = backend._get_credentials()
    assert creds[cred_id].secret_ref == "webhook_secret"


# ---------------------------------------------------------------------------
# Backend -- get_credentials_by_consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_get_credentials_by_consumer(
    backend: InMemoryAuthBackend,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "multi-cred", TrustTier.VERIFIED, [],
    )
    await backend.create_credential(consumer_id, "api_key", "key1")
    await backend.create_credential(consumer_id, "bearer", "token1")
    await backend.create_credential(consumer_id, "api_key", "key2")

    all_creds = await backend.get_credentials_by_consumer(consumer_id)
    assert len(all_creds) == 3

    api_key_creds = await backend.get_credentials_by_consumer(
        consumer_id, credential_type="api_key",
    )
    assert len(api_key_creds) == 2
    assert all(c.credential_type == "api_key" for c in api_key_creds)

    bearer_creds = await backend.get_credentials_by_consumer(
        consumer_id, credential_type="bearer",
    )
    assert len(bearer_creds) == 1


# ---------------------------------------------------------------------------
# Authenticator: correct key (via verifier registry)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_correct_key(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "api-user", TrustTier.IDENTIFIED, ["read"],
    )
    await backend.create_credential(
        consumer_id, "api_key", "valid-key-123",
    )

    auth_context = await authenticator.authenticate("valid-key-123")
    assert isinstance(auth_context, AuthContext)
    assert auth_context.consumer_id == consumer_id
    assert auth_context.trust_tier == TrustTier.IDENTIFIED
    assert "read" in auth_context.scopes
    assert auth_context.authenticated_via == "api_key"


# ---------------------------------------------------------------------------
# Authenticator: wrong key
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_wrong_key(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "api-user", TrustTier.IDENTIFIED, ["read"],
    )
    await backend.create_credential(
        consumer_id, "api_key", "valid-key-123",
    )

    with pytest.raises(AuthenticationError, match="Invalid credential"):
        await authenticator.authenticate("wrong-key-456")


# ---------------------------------------------------------------------------
# Authenticator: unregistered credential type is a construction-time error
# ---------------------------------------------------------------------------


def test_verifier_type_mismatch_is_construction_error(
    backend: InMemoryAuthBackend,
) -> None:
    """A verifier whose credential_type doesn't match its registry key
    is caught at construction time."""
    verifier = HashCredentialVerifier("api_key", backend)
    with pytest.raises(ValueError, match="reports credential_type"):
        Authenticator(backend, {"bearer": verifier})


# ---------------------------------------------------------------------------
# Authenticator: audit events emitted
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_audit_events_emitted_on_success(
    backend: InMemoryAuthBackend,
) -> None:
    sink = CollectingSink()
    verifiers = {
        "api_key": HashCredentialVerifier("api_key", backend),
    }
    authenticator = Authenticator(backend, verifiers, audit_sink=sink)

    consumer_id = await _mk_consumer(backend,
        "audit-user", TrustTier.VERIFIED, [],
    )
    await backend.create_credential(consumer_id, "api_key", "audit-key")

    await authenticator.authenticate("audit-key")

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == "success"
    assert event.credential_type == "api_key"
    assert event.reason is None


@pytest.mark.asyncio
async def test_audit_events_emitted_on_failure(
    backend: InMemoryAuthBackend,
) -> None:
    sink = CollectingSink()
    verifiers = {
        "api_key": HashCredentialVerifier("api_key", backend),
    }
    authenticator = Authenticator(backend, verifiers, audit_sink=sink)

    with pytest.raises(AuthenticationError):
        await authenticator.authenticate("nonexistent-key")

    assert len(sink.events) == 1
    event = sink.events[0]
    assert event.outcome == "failure"


# ---------------------------------------------------------------------------
# Authorizer: allowed scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_allowed_scope(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
    authorizer: Authorizer,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "writer", TrustTier.VERIFIED, ["read", "write"],
    )
    await backend.create_credential(
        consumer_id, "bearer", "token-abc",
    )

    auth_context = await authenticator.authenticate("token-abc")
    # Should not raise
    authorizer.authorize(auth_context, "write")


# ---------------------------------------------------------------------------
# Authorizer: disallowed scope
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authorize_disallowed_scope(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
    authorizer: Authorizer,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "reader", TrustTier.IDENTIFIED, ["read"],
    )
    await backend.create_credential(
        consumer_id, "api_key", "reader-key",
    )

    auth_context = await authenticator.authenticate("reader-key")
    with pytest.raises(AuthorizationError, match="lacks required scope"):
        authorizer.authorize(auth_context, "admin")


# ---------------------------------------------------------------------------
# Authorizer: scope vocabulary enforced
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_vocabulary(
    backend: InMemoryAuthBackend,
    authorizer: Authorizer,
) -> None:
    """Auth context with specific scopes from the vocabulary is correctly gated."""
    verifiers = {
        "api_key": HashCredentialVerifier("api_key", backend),
    }
    authenticator = Authenticator(backend, verifiers)

    consumer_id = await _mk_consumer(backend,
        "scoped-user",
        TrustTier.VERIFIED,
        [SCOPE_EVENTS_READ, SCOPE_EVENTS_WRITE],
    )
    await backend.create_credential(consumer_id, "api_key", "scoped-key")

    auth_context = await authenticator.authenticate("scoped-key")

    # Allowed scopes pass.
    authorizer.authorize(auth_context, SCOPE_EVENTS_READ)
    authorizer.authorize(auth_context, SCOPE_EVENTS_WRITE)

    # Missing scopes fail.
    with pytest.raises(AuthorizationError):
        authorizer.authorize(auth_context, SCOPE_SOURCES_MANAGE)
    with pytest.raises(AuthorizationError):
        authorizer.authorize(auth_context, SCOPE_SUBSCRIPTIONS_MANAGE)


def test_all_scopes_constant() -> None:
    """ALL_SCOPES contains exactly the defined scope constants."""
    assert frozenset({
        SCOPE_RUNS_READ,
        SCOPE_RUNS_MANAGE,
        SCOPE_INBOX_READ,
        SCOPE_INBOX_RESPOND,
        SCOPE_NOTIFICATIONS_READ,
        SCOPE_NOTIFICATIONS_MANAGE,
        SCOPE_TRACE_READ,
        SCOPE_EVENTS_READ,
        SCOPE_EVENTS_WRITE,
        SCOPE_CONFIG_READ,
        SCOPE_VALIDATE_READ,
        SCOPE_SOURCES_READ,
        SCOPE_SOURCES_MANAGE,
        SCOPE_SUBSCRIPTIONS_READ,
        SCOPE_SUBSCRIPTIONS_MANAGE,
        SCOPE_PRINCIPALS_READ,
        SCOPE_PRINCIPALS_MANAGE,
    }) == ALL_SCOPES


# ---------------------------------------------------------------------------
# Authenticator: disabled consumer
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_authenticate_disabled_consumer(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    consumer_id = await _mk_consumer(backend,
        "disabled-user", TrustTier.VERIFIED, ["read"],
    )
    await backend.create_credential(
        consumer_id, "api_key", "disabled-key",
    )

    # Disable the consumer
    await backend.disable_consumer(consumer_id)

    with pytest.raises(AuthenticationError, match="disabled"):
        await authenticator.authenticate("disabled-key")


# ---------------------------------------------------------------------------
# ASGI middleware
# ---------------------------------------------------------------------------


async def _echo_app(
    scope: dict,
    receive: object,
    send: object,
) -> None:
    """Simple ASGI app that returns 200 with auth context info."""
    auth_context = scope.get("state", {}).get("auth_context")
    body = json.dumps({
        "authenticated": auth_context is not None,
        "consumer_id": str(auth_context.consumer_id) if auth_context else None,
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

    async def __call__(self, message: dict) -> None:
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
    consumer_id = await _mk_consumer(backend,
        "mw-user", TrustTier.VERIFIED, ["api"],
    )
    await backend.create_credential(
        consumer_id, "bearer", "valid-mw-token",
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
async def test_middleware_lifespan_passthrough(
    authenticator: Authenticator,
) -> None:
    """Lifespan scopes pass through without auth."""
    called = False

    async def passthrough_app(
        scope: dict,
        receive: object,
        send: object,
    ) -> None:
        nonlocal called
        called = True

    app = auth_middleware(passthrough_app, authenticator)
    scope = {"type": "lifespan"}

    await app(scope, None, None)
    assert called


# ---------------------------------------------------------------------------
# WebSocket middleware
# ---------------------------------------------------------------------------


class _WsSendCapture:
    """Captures WebSocket ASGI send() calls for assertion."""

    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []

    async def __call__(self, message: dict[str, object]) -> None:
        self.messages.append(message)


class _WsReceiveStub:
    """Stub receive() that returns websocket.connect on first call.

    Tracks how many times receive() was called so tests can verify
    whether the connect message was consumed by the middleware.
    """

    def __init__(self) -> None:
        self.call_count = 0

    async def __call__(self) -> dict[str, object]:
        self.call_count += 1
        return {"type": "websocket.connect"}


@pytest.mark.asyncio
async def test_middleware_ws_authenticated(
    backend: InMemoryAuthBackend,
    authenticator: Authenticator,
) -> None:
    """WebSocket with valid bearer: AuthContext stored, inner app called,
    receive unconsumed so handler's accept() works."""
    consumer_id = await _mk_consumer(backend,
        "ws-user", TrustTier.VERIFIED, ["api"],
    )
    await backend.create_credential(
        consumer_id, "bearer", "ws-valid-token",
    )

    inner_called = False
    inner_scope_state: dict[str, object] = {}
    inner_receive_ref: list[object] = []

    async def ws_app(
        scope: dict[str, object],
        receive: object,
        send: object,
    ) -> None:
        nonlocal inner_called
        inner_called = True
        inner_scope_state.update(scope.get("state", {}))  # type: ignore[union-attr]
        inner_receive_ref.append(receive)

    app = auth_middleware(ws_app, authenticator)
    receive = _WsReceiveStub()
    send_capture = _WsSendCapture()

    scope: dict[str, object] = {
        "type": "websocket",
        "headers": [(b"authorization", b"Bearer ws-valid-token")],
    }
    await app(scope, receive, send_capture)

    # Inner app was called.
    assert inner_called

    # AuthContext was attached.
    auth_ctx = inner_scope_state.get("auth_context")
    assert auth_ctx is not None
    assert isinstance(auth_ctx, AuthContext)
    assert auth_ctx.consumer_id == consumer_id

    # receive was NOT consumed by the middleware -- the handler gets
    # the original receive so its accept() can read websocket.connect.
    assert receive.call_count == 0

    # The receive passed to the inner app is the original one.
    assert inner_receive_ref[0] is receive

    # Middleware did not send anything (no close, no accept -- that's
    # the handler's job).
    assert len(send_capture.messages) == 0


@pytest.mark.asyncio
async def test_middleware_ws_missing_auth(
    authenticator: Authenticator,
) -> None:
    """WebSocket with no Authorization header: close with 4001,
    inner app NOT called."""
    inner_called = False

    async def ws_app(
        scope: dict[str, object],
        receive: object,
        send: object,
    ) -> None:
        nonlocal inner_called
        inner_called = True

    app = auth_middleware(ws_app, authenticator)
    receive = _WsReceiveStub()
    send_capture = _WsSendCapture()

    scope: dict[str, object] = {
        "type": "websocket",
        "headers": [],
    }
    await app(scope, receive, send_capture)

    assert not inner_called

    # Middleware consumed websocket.connect before sending close.
    assert receive.call_count == 1

    # Sent websocket.close with code 4001.
    assert len(send_capture.messages) == 1
    close_msg = send_capture.messages[0]
    assert close_msg["type"] == "websocket.close"
    assert close_msg["code"] == 4001


@pytest.mark.asyncio
async def test_middleware_ws_invalid_token(
    authenticator: Authenticator,
) -> None:
    """WebSocket with invalid bearer token: close with 4001,
    inner app NOT called."""
    inner_called = False

    async def ws_app(
        scope: dict[str, object],
        receive: object,
        send: object,
    ) -> None:
        nonlocal inner_called
        inner_called = True

    app = auth_middleware(ws_app, authenticator)
    receive = _WsReceiveStub()
    send_capture = _WsSendCapture()

    scope: dict[str, object] = {
        "type": "websocket",
        "headers": [(b"authorization", b"Bearer bad-ws-token")],
    }
    await app(scope, receive, send_capture)

    assert not inner_called

    # Middleware consumed websocket.connect before sending close.
    assert receive.call_count == 1

    # Sent websocket.close with code 4001.
    assert len(send_capture.messages) == 1
    close_msg = send_capture.messages[0]
    assert close_msg["type"] == "websocket.close"
    assert close_msg["code"] == 4001


# ---------------------------------------------------------------------------
# HMAC verification: known test vectors
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hmac_verification_known_vector() -> None:
    """HMAC verification against a known signature test vector."""
    secret_key = "test-webhook-secret-key-2024"
    message = b"payload-body-content"
    expected_sig = hmac.new(
        secret_key.encode(),
        message,
        hashlib.sha256,
    ).hexdigest()

    registry = SecretRegistry({"webhook_secret": secret_key})
    provider = EnvMacProvider(registry)

    verdict = await provider.verify(
        key_ref="webhook_secret",
        message=message,
        signature=expected_sig,
        algorithm="sha256",
    )
    assert verdict.outcome == MacOutcome.MATCH
    assert verdict.secret_name == "webhook_secret"
    assert verdict.algorithm == "sha256"
    assert verdict.matched_version is None  # base key, not versioned


@pytest.mark.asyncio
async def test_hmac_verification_mismatch() -> None:
    """Wrong signature returns MISMATCH verdict."""
    registry = SecretRegistry({"webhook_secret": "real-key"})
    provider = EnvMacProvider(registry)

    verdict = await provider.verify(
        key_ref="webhook_secret",
        message=b"some data",
        signature="deadbeef",
        algorithm="sha256",
    )
    assert verdict.outcome == MacOutcome.MISMATCH


@pytest.mark.asyncio
async def test_hmac_verification_unknown_key() -> None:
    """Unknown key_ref returns MISMATCH (not an error)."""
    registry = SecretRegistry({"other_secret": "value"})
    provider = EnvMacProvider(registry)

    verdict = await provider.verify(
        key_ref="nonexistent_key",
        message=b"data",
        signature="sig",
        algorithm="sha256",
    )
    assert verdict.outcome == MacOutcome.MISMATCH


@pytest.mark.asyncio
async def test_hmac_unsupported_algorithm() -> None:
    """Unsupported algorithm raises ValueError."""
    registry = SecretRegistry({"key": "value"})
    provider = EnvMacProvider(registry)

    with pytest.raises(ValueError, match="Unsupported HMAC algorithm"):
        await provider.verify(
            key_ref="key",
            message=b"data",
            signature="sig",
            algorithm="md5",
        )


# ---------------------------------------------------------------------------
# HMAC key rotation: multi-version support
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hmac_rotation_old_key_matches() -> None:
    """During rotation, old key (versioned) still verifies."""
    old_key = "old-secret-2023"
    new_key = "new-secret-2024"
    message = b"rotate-test-payload"

    # Old key is at version 1, new key is at version 2.
    registry = SecretRegistry({
        "webhook_secret:1": old_key,
        "webhook_secret:2": new_key,
    })
    provider = EnvMacProvider(registry)

    # Sign with old key.
    old_sig = hmac.new(old_key.encode(), message, hashlib.sha256).hexdigest()

    verdict = await provider.verify(
        key_ref="webhook_secret",
        message=message,
        signature=old_sig,
        algorithm="sha256",
    )
    assert verdict.outcome == MacOutcome.MATCH
    assert verdict.matched_version == 1


@pytest.mark.asyncio
async def test_hmac_rotation_new_key_matches() -> None:
    """During rotation, new key (versioned) verifies."""
    old_key = "old-secret-2023"
    new_key = "new-secret-2024"
    message = b"rotate-test-payload"

    registry = SecretRegistry({
        "webhook_secret:1": old_key,
        "webhook_secret:2": new_key,
    })
    provider = EnvMacProvider(registry)

    # Sign with new key.
    new_sig = hmac.new(new_key.encode(), message, hashlib.sha256).hexdigest()

    verdict = await provider.verify(
        key_ref="webhook_secret",
        message=message,
        signature=new_sig,
        algorithm="sha256",
    )
    assert verdict.outcome == MacOutcome.MATCH
    assert verdict.matched_version == 2


# ---------------------------------------------------------------------------
# KeyedMacProvider protocol: no get/resolve method exists
# ---------------------------------------------------------------------------


def test_keyed_mac_provider_has_no_export_api() -> None:
    """The KeyedMacProvider protocol has no get-value or resolve method.

    This is a type-level assertion: key export is impossible by
    construction because the protocol only defines verify().
    """
    # Check the protocol's abstract methods.
    provider_methods = {
        name for name in dir(KeyedMacProvider)
        if not name.startswith("_")
    }
    # verify is the only public method.
    assert "verify" in provider_methods

    # Explicitly check that export-capable methods don't exist.
    assert "get" not in provider_methods
    assert "get_value" not in provider_methods
    assert "resolve" not in provider_methods
    assert "export" not in provider_methods
    assert "read" not in provider_methods


def test_env_mac_provider_has_no_export_api() -> None:
    """The EnvMacProvider implementation has no get-value method.

    Even though it holds a SecretRegistry internally, it never
    exposes the raw key value.
    """
    provider_methods = {
        name for name in dir(EnvMacProvider)
        if not name.startswith("_")
    }
    assert "verify" in provider_methods
    assert "get" not in provider_methods
    assert "get_value" not in provider_methods
    assert "resolve" not in provider_methods
    assert "export" not in provider_methods


# ---------------------------------------------------------------------------
# HMAC credential verifier: end-to-end through Authenticator
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_hmac_verifier_end_to_end() -> None:
    """HMAC credential goes through the full auth flow."""
    secret_key = "e2e-hmac-secret"
    message = "request-body"
    signature = hmac.new(
        secret_key.encode(),
        message.encode(),
        hashlib.sha256,
    ).hexdigest()

    registry = SecretRegistry({"webhook_key": secret_key})
    provider = EnvMacProvider(registry)

    backend = InMemoryAuthBackend()
    consumer_id = await _mk_consumer(backend,
        "hmac-consumer",
        TrustTier.VERIFIED,
        [SCOPE_EVENTS_WRITE],
    )

    # The identifier is hashed and stored.
    identifier = "hmac-client-id"
    await backend.create_credential(
        consumer_id,
        "hmac",
        identifier,
        secret_ref="webhook_key",
    )

    sink = CollectingSink()
    verifiers: dict[str, object] = {
        "api_key": HashCredentialVerifier("api_key", backend),
        "bearer": HashCredentialVerifier("bearer", backend),
        "hmac": HmacCredentialVerifier(provider, backend),
    }
    authenticator = Authenticator(backend, verifiers, audit_sink=sink)  # type: ignore[arg-type]

    # Present credential as "identifier:signature:message".
    raw = f"{identifier}:{signature}:{message}"
    auth_context = await authenticator.authenticate(raw)

    assert auth_context.consumer_id == consumer_id
    assert auth_context.authenticated_via == "hmac"
    assert SCOPE_EVENTS_WRITE in auth_context.scopes

    # Audit event was emitted.
    assert len(sink.events) == 1
    assert sink.events[0].outcome == "success"
    assert sink.events[0].credential_type == "hmac"


@pytest.mark.asyncio
async def test_hmac_verifier_wrong_signature() -> None:
    """HMAC with wrong signature fails authentication."""
    secret_key = "correct-secret"
    registry = SecretRegistry({"webhook_key": secret_key})
    provider = EnvMacProvider(registry)

    backend = InMemoryAuthBackend()
    consumer_id = await _mk_consumer(backend,
        "hmac-consumer", TrustTier.VERIFIED, [],
    )
    identifier = "hmac-client"
    await backend.create_credential(
        consumer_id, "hmac", identifier, secret_ref="webhook_key",
    )

    verifiers: dict[str, object] = {
        "hmac": HmacCredentialVerifier(provider, backend),
    }
    authenticator = Authenticator(backend, verifiers)  # type: ignore[arg-type]

    raw = f"{identifier}:wrong-signature:some-body"
    with pytest.raises(AuthenticationError, match="HMAC signature mismatch"):
        await authenticator.authenticate(raw)


@pytest.mark.asyncio
async def test_hmac_verifier_no_secret_ref() -> None:
    """HMAC credential without secret_ref fails."""
    registry = SecretRegistry({"key": "value"})
    provider = EnvMacProvider(registry)

    backend = InMemoryAuthBackend()
    consumer_id = await _mk_consumer(backend,
        "hmac-consumer", TrustTier.VERIFIED, [],
    )
    identifier = "hmac-client"
    # No secret_ref.
    await backend.create_credential(
        consumer_id, "hmac", identifier,
    )

    verifiers: dict[str, object] = {
        "hmac": HmacCredentialVerifier(provider, backend),
    }
    authenticator = Authenticator(backend, verifiers)  # type: ignore[arg-type]

    raw = f"{identifier}:sig:body"
    with pytest.raises(AuthenticationError, match="no secret_ref"):
        await authenticator.authenticate(raw)


# ---------------------------------------------------------------------------
# Scope-lacking auth context denied
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_scope_lacking_auth_context_denied() -> None:
    """An auth context without the required scope is denied by the Authorizer."""
    backend = InMemoryAuthBackend()
    consumer_id = await _mk_consumer(backend,
        "limited-user",
        TrustTier.IDENTIFIED,
        [SCOPE_EVENTS_READ],
    )
    await backend.create_credential(consumer_id, "api_key", "limited-key")

    verifiers = {"api_key": HashCredentialVerifier("api_key", backend)}
    authenticator = Authenticator(backend, verifiers)
    authorizer = Authorizer()

    auth_context = await authenticator.authenticate("limited-key")
    authorizer.authorize(auth_context, SCOPE_EVENTS_READ)  # passes

    with pytest.raises(AuthorizationError, match="lacks required scope"):
        authorizer.authorize(auth_context, SCOPE_EVENTS_WRITE)

    with pytest.raises(AuthorizationError, match="lacks required scope"):
        authorizer.authorize(auth_context, SCOPE_SOURCES_MANAGE)
