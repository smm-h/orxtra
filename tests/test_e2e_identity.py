"""End-to-end proof of the identity feature's story.

This is the single narrative file that proves identity attribution and
enforcement across every interface the feature touches, against a real
PostgreSQL database (via testcontainers). Pieces are covered piecemeal in
per-module tests; this file proves they compose end to end.

Scenarios (one test each):

1. An authenticated MCP tool call (``fire_event``) through the real FastMCP
   app behind the real auth wall attributes the event to the CONSUMER's
   principal.
2. Anonymous HTTP to ``/mcp`` and ``/a2a`` (through the compositor with the
   auth wall) is rejected with 401.
3. A signed webhook is attributed to the SOURCE principal; a slug-filtered
   subscription owned by a consumer principal matches it, and the
   EventAction-derived event is attributed to the SUBSCRIPTION OWNER.
4. Answering an inbox item through dispatch with a consumer context stamps
   ``resolved_by`` with the consumer's principal.
5. ``delete_principal``: a consumer that only owns a subscription CASCADE-
   deletes; a consumer that fired an event is pinned (PrincipalInUseError).
6. AG-UI per-run access: the creator streams its own run, another consumer
   gets 403, and a SYSTEM-tier operator streams any run.
7. A CLI-as-system dispatch (operator context) lands attribution on the
   system principal.
8. MCP session-manager init behind the auth wall, proven at two layers:
   (8a) driving the auth-wrapped MCP mount's lifespan DIRECTLY initializes the
   StreamableHTTP session manager behind the auth middleware (version-
   independent; guards the middleware seam), and (8b) driving build_app's FULL
   composited lifespan initializes it THROUGH the compositor via fastware's
   mount-lifespan forwarding (the real deployment path). In both an
   authenticated handshake succeeds while an anonymous request is rejected 401.
   The compositor unit tests catch "Task group is not initialized" as a
   workaround; these prove the real thing.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import hmac
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Self
from uuid import UUID

import httpx
import pytest
import uuid6
from fastware import create_app
from fastware.testing import AsyncTestClient
from mcp.client.streamable_http import streamable_http_client
from orxtra.api._lifecycle import ServerConfig, build_app
from orxtra.auth import (
    AuthBackend,
    Authenticator,
    HashCredentialVerifier,
    HmacCredentialVerifier,
    auth_middleware,
)
from orxtra.identity import PgPrincipalStorage, PrincipalInUseError
from orxtra.protocols import (
    ALL_SCOPES,
    KIND_CONSUMER,
    KIND_SYSTEM,
    SYSTEM_PRINCIPAL_EXTERNAL_REF,
    AuthContext,
    FilterPredicate,
    TrustTier,
)
from orxtra.services import DispatchContext, dispatch, subscribe

from mcp import ClientSession
from tests.pg_fixtures import skip_no_docker

if TYPE_CHECKING:
    import asyncpg
    from orxtra.protocols import Principal

pytestmark = skip_no_docker


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _LifespanRunner:
    """Minimal ASGI lifespan driver.

    Runs an app's lifespan as a background task: sends ``lifespan.startup`` on
    enter (holding the task open so mounted session managers stay initialized),
    and ``lifespan.shutdown`` on exit. Needed because httpx's ``ASGITransport``
    never emits lifespan events, yet the FastMCP StreamableHTTP session manager
    only initializes its task group inside its lifespan.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._to: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._from: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._task: asyncio.Task[None] | None = None

    async def _receive(self) -> dict[str, Any]:
        return await self._to.get()

    async def _send(self, message: dict[str, Any]) -> None:
        await self._from.put(message)

    async def __aenter__(self) -> Self:
        self._task = asyncio.create_task(
            self._app({"type": "lifespan", "state": {}}, self._receive, self._send),
        )
        await self._to.put({"type": "lifespan.startup"})
        message = await self._from.get()
        assert message["type"] == "lifespan.startup.complete", message
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self._to.put({"type": "lifespan.shutdown"})
        with contextlib.suppress(Exception):
            await asyncio.wait_for(self._from.get(), timeout=5)
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(Exception, asyncio.CancelledError):
                await self._task


async def _seed_system(pool: asyncpg.Pool) -> Principal:
    storage = PgPrincipalStorage(pool)
    return await storage.mint_principal(
        KIND_SYSTEM, SYSTEM_PRINCIPAL_EXTERNAL_REF, "system",
    )


async def _register_consumer(
    pool: asyncpg.Pool,
    *,
    name: str,
    tier: TrustTier,
    scopes: list[str],
    token: str,
    token_type: str = "bearer",  # noqa: S107 -- credential type label, not a secret
    secret_ref: str | None = None,
) -> tuple[Principal, UUID]:
    """Mint a consumer principal + persist a consumer and a credential.

    The consumer id equals the principal's external_ref so the resolver can
    recover the principal from the AuthContext's ``consumer_id``.
    """
    storage = PgPrincipalStorage(pool)
    ref = uuid6.uuid7()
    principal = await storage.mint_principal(KIND_CONSUMER, ref, name)
    backend = AuthBackend(pool)
    consumer_id = await backend.create_consumer(
        name, tier, scopes, consumer_id=ref, principal_id=principal.id,
    )
    await backend.create_credential(
        consumer_id, token_type, token, secret_ref=secret_ref,
    )
    return principal, consumer_id


async def _create_run(pool: asyncpg.Pool, creator: Principal) -> UUID:
    """Insert a run attributed to ``creator`` (events FK into runs.run_id)."""
    run_id: UUID = await pool.fetchval(
        "INSERT INTO runs (intent, autonomy_level, created_by)"
        " VALUES ('e2e', 'medium', $1) RETURNING id",
        creator.id,
    )
    return run_id


def _consumer_ctx(consumer_id: UUID) -> AuthContext:
    return AuthContext(
        id=uuid6.uuid7(),
        consumer_id=consumer_id,
        scopes=ALL_SCOPES,
        trust_tier=TrustTier.IDENTIFIED,
        authenticated_via="e2e-consumer",
        issued_at=datetime.now(UTC),
        expires_at=None,
    )


def _operator_ctx() -> AuthContext:
    """A SYSTEM-tier operator context -- what the CLI presents on every call."""
    return AuthContext(
        id=uuid6.uuid7(),
        consumer_id=None,
        scopes=ALL_SCOPES,
        trust_tier=TrustTier.SYSTEM,
        authenticated_via="e2e-operator",
        issued_at=datetime.now(UTC),
        expires_at=None,
    )


def _bearer_authenticator(pool: asyncpg.Pool) -> Authenticator:
    backend = AuthBackend(pool)
    verifiers: dict[str, HashCredentialVerifier | HmacCredentialVerifier] = {
        "bearer": HashCredentialVerifier("bearer", backend),
    }
    return Authenticator(backend, verifiers)


def _walled_mcp_app(
    pool: asyncpg.Pool,
    storage: PgPrincipalStorage,
    authenticator: Authenticator,
) -> Any:
    """Build the auth-wrapped MCP streamable-HTTP app exactly as the compositor
    mounts it (root-relative path + auth wall), plus test-only transport
    security relaxation so the in-process ``testserver`` host is accepted.
    """
    from mcp.server.transport_security import TransportSecuritySettings
    from orxtra.mcp import MCPServer

    ctx = DispatchContext(pool=pool, principal_storage=storage)
    server = MCPServer(pool=pool, dispatch_context=ctx)
    server.fastmcp.settings.streamable_http_path = "/"
    server.fastmcp.settings.transport_security = TransportSecuritySettings(
        enable_dns_rebinding_protection=False,
    )
    mcp_app = server.fastmcp.streamable_http_app()
    return auth_middleware(mcp_app, authenticator)


def _mcp_http_client(
    app: Any, token: str, base_url: str = "http://testserver",
) -> httpx.AsyncClient:
    """An httpx client that drives the in-process ASGI app with the bearer
    token attached, handed to the MCP streamable-HTTP client.

    ``base_url`` defaults to the ``testserver`` host used by the direct-mount
    tests (which disable DNS-rebinding protection). The full-compositor test
    passes a ``localhost:<port>`` URL that FastMCP's default transport security
    allowlists.
    """
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        headers={"authorization": f"Bearer {token}"},
        base_url=base_url,
        timeout=30,
    )


def _compositor(pool: asyncpg.Pool, authenticator: Authenticator | None) -> Any:
    """Build the full compositor app with a real pool-backed dispatch context."""
    from a2a.types.a2a_pb2 import AgentCapabilities, AgentCard, AgentInterface
    from orxtra.a2a import SkillRegistry
    from orxtra.api import CompositorConfig, create_compositor

    storage = PgPrincipalStorage(pool)
    card = AgentCard(
        name="e2e-agent",
        description="E2E",
        version="0.0.1",
        supported_interfaces=[AgentInterface(url="http://testserver/a2a")],
        capabilities=AgentCapabilities(streaming=True),
    )
    config = CompositorConfig(
        dispatch_context=DispatchContext(pool=pool, principal_storage=storage),
        agent_card=card,
        skill_registry=SkillRegistry([]),
        authenticator=authenticator,
    )
    return create_compositor(config)


# ---------------------------------------------------------------------------
# 1. Authenticated MCP tool call -> consumer principal
# ---------------------------------------------------------------------------


async def test_mcp_fire_event_attributes_consumer_principal(
    pg_pool: asyncpg.Pool,
) -> None:
    """An authenticated MCP ``fire_event`` call, driven over the real
    StreamableHTTP protocol behind the real auth wall, attributes the event to
    the calling CONSUMER's principal.
    """
    system = await _seed_system(pg_pool)
    run_id = await _create_run(pg_pool, system)
    token = "mcp-consumer-token"
    consumer, _cid = await _register_consumer(
        pg_pool, name="acme", tier=TrustTier.VERIFIED,
        scopes=["events:read", "events:write"], token=token,
    )

    storage = PgPrincipalStorage(pg_pool)
    walled = _walled_mcp_app(pg_pool, storage, _bearer_authenticator(pg_pool))

    # Nested (not combined) with-blocks: the MCP client scopes anyio task
    # groups per context, so the session must close inside the transport.
    async with _LifespanRunner(walled), _mcp_http_client(walled, token) as client:
        async with streamable_http_client(  # noqa: SIM117
            "http://testserver/", http_client=client,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "fire_event",
                    {
                        "run_id": str(run_id),
                        "event_name": "e2e.mcp_fired",
                        "payload": {"via": "mcp"},
                    },
                )
        # The tool returns [event_id, inserted]; it must not be an error.
        assert result.isError is False, result.content
        payload = result.content[0].text  # type: ignore[union-attr]
        assert "true" in payload.lower()

    row = await pg_pool.fetchrow(
        "SELECT principal_id FROM events WHERE event_type = 'e2e.mcp_fired'",
    )
    assert row is not None, "the MCP tool call must have fired an event"
    assert row["principal_id"] == consumer.id, (
        "an MCP fire_event must attribute the event to the calling consumer's "
        "principal, not the system principal"
    )


# ---------------------------------------------------------------------------
# 2. Anonymous HTTP -> 401 through the compositor auth wall
# ---------------------------------------------------------------------------


async def test_anonymous_http_to_mcp_and_a2a_is_401(
    pg_pool: asyncpg.Pool,
) -> None:
    """With the auth wall configured, anonymous requests to ``/mcp`` and
    ``/a2a`` are rejected with 401 before reaching either sub-app.
    """
    await _seed_system(pg_pool)
    app = _compositor(pg_pool, _bearer_authenticator(pg_pool))

    async with AsyncTestClient(app) as client:
        mcp_resp = await client.get("/mcp")
        assert mcp_resp.status_code == 401

        a2a_resp = await client.post(
            "/a2a",
            content=b'{"jsonrpc":"2.0","method":"message/send","id":1}',
            headers={"content-type": "application/json"},
        )
        assert a2a_resp.status_code == 401


# ---------------------------------------------------------------------------
# 3. Signed webhook -> source principal -> owner-attributed derived event
# ---------------------------------------------------------------------------


def _sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


async def test_webhook_source_to_slug_subscription_to_owner(
    pg_pool: asyncpg.Pool,
) -> None:
    """A signed webhook is attributed to the SOURCE principal; a slug-filtered
    subscription owned by a consumer matches it, and its EventAction re-fires a
    derived event attributed to the SUBSCRIPTION OWNER.
    """
    from orxtra.dispatch import PgDispatchBackend
    from orxtra.incoming import create_incoming_router
    from orxtra.secrets import EnvMacProvider, SecretRegistry
    from orxtra.services import create_dispatch_worker

    await _seed_system(pg_pool)
    storage = PgPrincipalStorage(pg_pool)
    secret = "webhook-shared-secret"
    slug = "gh-e2e"

    # A consumer + hmac credential guards the webhook endpoint.
    _cred_consumer, webhook_consumer_id = await _register_consumer(
        pg_pool, name="gh-webhook", tier=TrustTier.IDENTIFIED,
        scopes=["events:write"], token="gh-hmac-id",
        token_type="hmac", secret_ref="webhook_secret",
    )
    cred_id = await pg_pool.fetchval(
        "SELECT id FROM credentials WHERE consumer_id = $1", webhook_consumer_id,
    )

    # Create the source (operator context) -- mints the source principal.
    operator = DispatchContext(
        pool=pg_pool,
        dispatch_backend=PgDispatchBackend(pg_pool),
        principal_storage=storage,
        auth_context=_operator_ctx(),
    )
    source_id = await dispatch(
        operator,
        "create_source",
        {
            "slug": slug,
            "name": "GitHub E2E",
            "credential_id": str(cred_id),
            "config": {
                "event_type_source": "header",
                "event_type_field": "X-GitHub-Event",
                "signature_header": "X-Hub-Signature-256",
                "idempotency_header": "X-GitHub-Delivery",
            },
        },
    )
    source_principal = await storage.get_principal_by_ref("source", source_id)
    assert source_principal is not None

    # A consumer owns a slug-filtered subscription whose EventAction re-fires.
    owner = await storage.mint_principal(
        KIND_CONSUMER, uuid6.uuid7(), "sub-owner",
    )
    backend = PgDispatchBackend(pg_pool)
    await subscribe(
        backend,
        owner,
        FilterPredicate(sources=[slug]),
        [{"action": {"event_type": "e2e.derived_from_webhook", "data": {}}}],
    )

    # Deliver a signed webhook through the real receiver.
    registry = SecretRegistry({"webhook_secret": secret})
    mac_provider = EnvMacProvider(registry)
    auth_backend = AuthBackend(pg_pool)
    authenticator = Authenticator(
        auth_backend,
        {"hmac": HmacCredentialVerifier(mac_provider, auth_backend)},
    )
    router = create_incoming_router(
        pool=pg_pool,
        dispatch_backend=backend,
        authenticator=authenticator,
        principal_storage=storage,
    )
    body = b'{"action":"opened"}'
    signature = _sign(body, secret)
    incoming_app = create_app(router)
    async with AsyncTestClient(incoming_app) as client:
        resp = await client.post(
            f"/events/{slug}",
            content=body,
            headers={
                "content-type": "application/json",
                "X-GitHub-Event": "e2e.push",
                "X-Hub-Signature-256": f"sha256={signature}",
                "X-GitHub-Delivery": "e2e-delivery-1",
            },
        )
        assert resp.status_code == 202, resp.text

    # The webhook event is attributed to the SOURCE principal.
    webhook_row = await pg_pool.fetchrow(
        "SELECT principal_id FROM events WHERE event_type = 'e2e.push'",
    )
    assert webhook_row is not None, "the webhook must have fired an event"
    assert webhook_row["principal_id"] == source_principal.id, (
        "a signed webhook event must be attributed to its source principal"
    )

    # Run the dispatch worker; the slug filter matches the source-attributed
    # event and the EventAction re-fires a derived event.
    worker = await create_dispatch_worker(pg_pool, poll_interval=0.1)
    task = asyncio.create_task(worker.run())
    await asyncio.sleep(0.8)
    await worker.stop()
    await task

    derived = await pg_pool.fetchrow(
        "SELECT principal_id FROM events WHERE event_type = 'e2e.derived_from_webhook'",
    )
    assert derived is not None, (
        "the slug-filtered subscription's EventAction must re-fire a derived event"
    )
    assert derived["principal_id"] == owner.id, (
        "the derived event must be attributed to the subscription OWNER, "
        "not the source or system principal"
    )
    # And the derived event is NOT re-attributed to the source (no loop).
    assert derived["principal_id"] != source_principal.id


# ---------------------------------------------------------------------------
# 4. Inbox resolution via dispatch -> resolved_by == consumer principal
# ---------------------------------------------------------------------------


async def test_inbox_respond_via_dispatch_stamps_consumer_principal(
    pg_pool: asyncpg.Pool,
) -> None:
    """Answering an inbox item through dispatch with a consumer context stamps
    ``resolved_by`` with that consumer's principal.
    """
    from orxtra.trace import TraceWriter

    system = await _seed_system(pg_pool)
    run_id = await _create_run(pg_pool, system)
    consumer, consumer_id = await _register_consumer(
        pg_pool, name="responder", tier=TrustTier.IDENTIFIED,
        scopes=["inbox:respond"], token="inbox-token",
    )

    writer = TraceWriter(pg_pool)
    item_id = await writer.create_inbox_item(
        run_id=run_id,
        decision_type="approval",
        question="proceed?",
        options=[],
        assumed_option=None,
        work_proceeding=None,
        contradiction_impact=None,
        tags=[],
    )

    ctx = DispatchContext(
        pool=pg_pool,
        principal_storage=PgPrincipalStorage(pg_pool),
        auth_context=_consumer_ctx(consumer_id),
    )
    await dispatch(
        ctx,
        "respond_to_inbox",
        {"item_id": str(item_id), "answer": "yes"},
    )

    row = await pg_pool.fetchrow(
        "SELECT status, resolved_by FROM inbox_items WHERE id = $1", item_id,
    )
    assert row is not None
    assert row["status"] == "answered"
    assert row["resolved_by"] == consumer.id, (
        "an inbox answer via dispatch must record the responding consumer's "
        "principal in resolved_by"
    )


# ---------------------------------------------------------------------------
# 5. delete_principal lifecycle: CASCADE vs RESTRICT
# ---------------------------------------------------------------------------


async def test_delete_principal_cascade_and_restrict(
    pg_pool: asyncpg.Pool,
) -> None:
    """A consumer that only owns a subscription CASCADE-deletes; a consumer
    that fired an event is pinned (PrincipalInUseError).
    """
    from orxtra.dispatch import PgDispatchBackend

    await _seed_system(pg_pool)
    storage = PgPrincipalStorage(pg_pool)
    backend = PgDispatchBackend(pg_pool)

    # (a) subscription owner -> CASCADE.
    owner = await storage.mint_principal(
        KIND_CONSUMER, uuid6.uuid7(), "cascade-owner",
    )
    sub_id = await subscribe(
        backend,
        owner,
        FilterPredicate(event_types=["e2e.owned"]),
        [{"action": {"message": "hi", "level": "info"}}],
    )
    await storage.delete_principal(owner.id)
    assert await storage.get_principal(owner.id) is None
    gone = await pg_pool.fetchrow(
        "SELECT id FROM subscriptions WHERE id = $1", sub_id,
    )
    assert gone is None, "deleting the owner must CASCADE-delete its subscription"

    # (b) event actor -> RESTRICT (PrincipalInUseError).
    actor = await storage.mint_principal(KIND_CONSUMER, uuid6.uuid7(), "actor")
    await pg_pool.execute(
        "INSERT INTO events (event_type, principal_id) VALUES ($1, $2)",
        "e2e.history", actor.id,
    )
    with pytest.raises(PrincipalInUseError) as exc_info:
        await storage.delete_principal(actor.id)
    assert str(actor.id) in str(exc_info.value)
    assert await storage.get_principal(actor.id) is not None


# ---------------------------------------------------------------------------
# 6. AG-UI per-run access: creator / other consumer / operator
# ---------------------------------------------------------------------------


async def test_agui_run_access_creator_other_operator(
    pg_pool: asyncpg.Pool,
) -> None:
    """Through the compositor's auth wall + AG-UI ownership check: the run
    creator streams (200), an unrelated consumer gets 403, and a SYSTEM-tier
    operator streams any run (200).
    """
    await _seed_system(pg_pool)
    creator, _ = await _register_consumer(
        pg_pool, name="agui-creator", tier=TrustTier.IDENTIFIED,
        scopes=["runs:read"], token="creator-token",
    )
    _other, _ = await _register_consumer(
        pg_pool, name="agui-other", tier=TrustTier.IDENTIFIED,
        scopes=["runs:read"], token="other-token",
    )
    _operator, _ = await _register_consumer(
        pg_pool, name="agui-operator", tier=TrustTier.SYSTEM,
        scopes=["runs:read"], token="operator-token",
    )
    run_id = await _create_run(pg_pool, creator)

    app = _compositor(pg_pool, _bearer_authenticator(pg_pool))
    async with AsyncTestClient(app) as client:
        # Creator streams its own run.
        async with asyncio.timeout(5), client.stream(
            "GET", "/ag-ui/events",
            params={"run_id": str(run_id)},
            headers={"authorization": "Bearer creator-token"},
        ) as resp:
            assert resp.status_code == 200

        # An unrelated consumer is forbidden.
        other_resp = await client.get(
            "/ag-ui/events",
            params={"run_id": str(run_id)},
            headers={"authorization": "Bearer other-token"},
        )
        assert other_resp.status_code == 403

        # The SYSTEM-tier operator streams any run.
        async with asyncio.timeout(5), client.stream(
            "GET", "/ag-ui/events",
            params={"run_id": str(run_id)},
            headers={"authorization": "Bearer operator-token"},
        ) as op_resp:
            assert op_resp.status_code == 200


# ---------------------------------------------------------------------------
# 7. CLI-as-system: operator dispatch -> attribution on the system principal
# ---------------------------------------------------------------------------


async def test_cli_as_system_dispatch_attributes_system(
    pg_pool: asyncpg.Pool,
) -> None:
    """A dispatch made through a SYSTEM-tier operator context (the CLI) lands
    attribution on the singleton system principal.
    """
    system = await _seed_system(pg_pool)
    run_id = await _create_run(pg_pool, system)

    ctx = DispatchContext(
        pool=pg_pool,
        principal_storage=PgPrincipalStorage(pg_pool),
        auth_context=_operator_ctx(),
    )
    await dispatch(
        ctx,
        "fire_event",
        {"run_id": str(run_id), "event_name": "e2e.cli_fired", "payload": {}},
    )

    row = await pg_pool.fetchrow(
        "SELECT principal_id FROM events WHERE event_type = 'e2e.cli_fired'",
    )
    assert row is not None
    assert row["principal_id"] == system.id, (
        "an operator (CLI-as-system) dispatch must attribute to the system "
        "principal"
    )


# ---------------------------------------------------------------------------
# 8. MCP session-manager init behind the auth wall
#
# Two complementary proofs of the same story at different layers:
#   8a (this test) -- the MIDDLEWARE LAYER: drive the auth-wrapped MCP mount
#      DIRECTLY (bypassing the compositor). Version-independent: it works even
#      without fastware's mount-lifespan forwarding, so it isolates and guards
#      the auth_middleware + MCP session-manager seam specifically.
#   8b (next test) -- the REAL COMPOSITED PATH: drive build_app's full lifespan
#      and let fastware (>= 0.5.0) forward it to the /mcp mount. This is the
#      path a real deployment takes, newly possible now that fastware forwards
#      lifespan to mounted sub-apps.
# ---------------------------------------------------------------------------


async def test_mcp_lifespan_initializes_session_manager_behind_auth_wall(
    pg_pool: asyncpg.Pool,
) -> None:
    """8a -- MIDDLEWARE LAYER. Drive the auth-wrapped MCP mount's lifespan
    DIRECTLY (not through the compositor) and prove its StreamableHTTP session
    manager initializes BEHIND the auth middleware.

    The compositor unit tests catch "Task group is not initialized" as a
    workaround because they never run the mounted app's lifespan. Here the
    mount's lifespan is driven for real: an authenticated MCP ``initialize``
    handshake succeeds (the session manager handled it), while an anonymous
    request is still rejected by the wall. This guards the middleware/session-
    manager seam independently of fastware's mount-lifespan forwarding; the
    full composited path is proven separately in test 8b.
    """
    await _seed_system(pg_pool)
    token = "carried-gap-token"
    await _register_consumer(
        pg_pool, name="gap", tier=TrustTier.VERIFIED,
        scopes=["events:read"], token=token,
    )
    storage = PgPrincipalStorage(pg_pool)
    walled = _walled_mcp_app(pg_pool, storage, _bearer_authenticator(pg_pool))

    async with _LifespanRunner(walled):
        # Anonymous request is rejected by the wall (never reaches the manager).
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=walled),
            base_url="http://testserver",
        ) as anon:
            anon_resp = await anon.get("/")
            assert anon_resp.status_code == 401

        # Authenticated handshake reaches -- and is served by -- the session
        # manager, proving its task group initialized behind the middleware.
        async with _mcp_http_client(walled, token) as client:  # noqa: SIM117
            async with streamable_http_client(
                "http://testserver/", http_client=client,
            ) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    init_result = await session.initialize()
                    assert init_result.serverInfo.name == "orxtra-mcp"


async def test_mcp_session_manager_initializes_through_full_compositor(
    pg_pool: asyncpg.Pool,
    pg_container: Any,
) -> None:
    """8b -- REAL COMPOSITED PATH. Drive ``build_app``'s FULL lifespan and prove
    the MCP StreamableHTTP session manager initializes THROUGH the compositor.

    Unlike 8a (which drives the mount directly), this exercises the whole
    production stack: build_app -> orxtra infrastructure lifespan -> compositor
    lifespan -> fastware (>= 0.5.0) mount-lifespan forwarding -> MCP session-
    manager task-group init. An authenticated ``initialize`` handshake to
    ``/mcp`` succeeds (proving the mount came up via forwarding), while an
    anonymous request to ``/mcp`` is still rejected 401 by the wall.

    The MCP mount uses FastMCP's default transport security, which allowlists
    ``localhost:*``/``127.0.0.1:*`` hosts, so the in-process client speaks to a
    ``localhost:<port>`` base URL rather than the ``testserver`` host that 8a
    accepts by disabling DNS-rebinding protection.
    """
    consumer, _cid = await _register_consumer(
        pg_pool, name="full-composited", tier=TrustTier.VERIFIED,
        scopes=["events:read"], token=(token := "full-composited-token"),
    )
    db_url = pg_container.get_connection_url().replace(
        "postgresql+psycopg2://", "postgresql://",
    )
    # build_app's lifespan seeds the system principal and verifies the schema
    # (already applied by the pg_pool fixture) against this same database.
    server_config = ServerConfig(
        db_url=db_url,
        port=8080,
        authenticator=_bearer_authenticator(pg_pool),
    )
    app = build_app(server_config)

    async with _LifespanRunner(app):
        # Anonymous request to /mcp is rejected by the wall (never reaches the
        # mounted MCP app's host validation).
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://localhost:8080",
        ) as anon:
            anon_resp = await anon.get("/mcp")
            assert anon_resp.status_code == 401

        # Authenticated handshake reaches -- and is served by -- the session
        # manager mounted at /mcp, proving fastware forwarded the compositor's
        # lifespan into the mount and its task group initialized.
        client = _mcp_http_client(app, token, "http://localhost:8080")
        async with client:  # noqa: SIM117
            async with streamable_http_client(
                "http://localhost:8080/mcp", http_client=client,
            ) as (read, write, _get_session_id):
                async with ClientSession(read, write) as session:
                    init_result = await session.initialize()
                    assert init_result.serverInfo.name == "orxtra-mcp"

    # The consumer that performed the handshake is a real registered principal.
    assert consumer.kind == KIND_CONSUMER
