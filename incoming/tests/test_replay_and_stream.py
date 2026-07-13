"""Tests for the replay and SSE stream endpoints.

Covers:
- Replay: returns events in order, respects since cursor, respects limit
- SSE stream auth: missing auth, wrong token, unknown slug
- SSE generator: no-loss during connect window, Last-Event-ID resume,
  deduplication, fetch-on-notify, source filtering, event format
- SSE stream not registered without event_bus
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import uuid6
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
from orxtra.incoming._stream import (
    _format_sse_event,
    _sse_generator,
    stream_handler,
)
from orxtra.protocols import AuthContext, Source, TrustTier
from orxtra.secrets import SecretRegistry
from orxtra.secrets._mac_provider import EnvMacProvider
from orxtra.trace import EVENTS_CHANNEL, InMemoryEventBus

# ---------------------------------------------------------------------------
# Test data
# ---------------------------------------------------------------------------

SLUG = "test-source"
BEARER_TOKEN = "test-bearer-token-replay"


def _make_event(
    event_id: UUID | None = None,
    event_type: str = "test.event",
    source: str = SLUG,
    data: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create an event dict matching the replay() return format."""
    if event_id is None:
        event_id = uuid6.uuid7()
    return {
        "id": event_id,
        "run_id": None,
        "task_id": None,
        "event_type": event_type,
        "source": source,
        "data": data or {"seq": str(event_id)},
        "created_at": datetime.now(tz=UTC),
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def dispatch_backend() -> InMemoryDispatchBackend:
    return InMemoryDispatchBackend()


@pytest.fixture
def auth_backend() -> InMemoryAuthBackend:
    return InMemoryAuthBackend()


@pytest.fixture
def secret_registry() -> SecretRegistry:
    return SecretRegistry({})


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
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


@pytest.fixture
async def source_with_bearer(
    dispatch_backend: InMemoryDispatchBackend,
    auth_backend: InMemoryAuthBackend,
) -> Source:
    """Create a source with bearer credential."""
    consumer_id = await auth_backend.create_consumer(
        "replay-consumer", TrustTier.IDENTIFIED, ["events:read"],
    )
    cred_id = await auth_backend.create_credential(
        consumer_id,
        "bearer",
        BEARER_TOKEN,
    )

    now = datetime.now(tz=UTC)
    source = Source(
        id=UUID("00000000-0000-0000-0000-000000000010"),
        slug=SLUG,
        name="Test Source",
        credential_id=cred_id,
        config={
            "event_type_source": "constant",
            "event_type_field": "test.event",
        },
        created_at=now,
    )
    await dispatch_backend.create_source(source)
    return source


@pytest.fixture
async def source_no_credential(
    dispatch_backend: InMemoryDispatchBackend,
) -> Source:
    """Create a source without credentials."""
    now = datetime.now(tz=UTC)
    source = Source(
        id=UUID("00000000-0000-0000-0000-000000000011"),
        slug="no-cred-source",
        name="No Credential Source",
        credential_id=None,
        config={},
        created_at=now,
    )
    await dispatch_backend.create_source(source)
    return source


def _make_app(
    dispatch_backend: InMemoryDispatchBackend,
    authenticator: Authenticator,
    event_bus: InMemoryEventBus | None = None,
) -> Any:
    """Build a minimal ASGI app with the incoming router."""
    mock_pool = AsyncMock()
    incoming_router = create_incoming_router(
        pool=mock_pool,
        dispatch_backend=dispatch_backend,
        authenticator=authenticator,
        event_bus=event_bus,
    )
    root = Router()
    root.include_router(incoming_router, prefix="/incoming")
    return create_app(root)


def _auth_headers() -> dict[str, str]:
    """Standard bearer auth headers for test requests."""
    return {"Authorization": f"Bearer {BEARER_TOKEN}"}


def _sentinel_auth_context() -> AuthContext:
    """Build a distinctive AuthContext for capture-propagation assertions."""
    return AuthContext(
        id=UUID("cccccccc-cccc-cccc-cccc-cccccccccccc"),
        consumer_id=UUID("dddddddd-dddd-dddd-dddd-dddddddddddd"),
        scopes=frozenset({"events:read"}),
        trust_tier=TrustTier.IDENTIFIED,
        authenticated_via="bearer",
        issued_at=datetime.now(tz=UTC),
        expires_at=None,
    )


class _FakeRequest:
    """Minimal request stand-in for calling stream_handler directly.

    Calling the handler directly (rather than over HTTP) avoids hanging on
    the infinite SSE generator: we only need the handler to reach the
    connect log before returning its StreamResponse.
    """

    def __init__(self, slug: str, headers: dict[str, str]) -> None:
        self.path_params: dict[str, str] = {"slug": slug}
        self._headers = {k.lower(): v for k, v in headers.items()}

    def header(self, name: str) -> str | None:
        return self._headers.get(name.lower())


# ---------------------------------------------------------------------------
# Replay endpoint tests
# ---------------------------------------------------------------------------


class TestReplayReturnsEventsInOrder:
    """GET /events/{slug}/replay returns events ordered by ID."""

    async def test_returns_events_ordered(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        event_ids = [uuid6.uuid7() for _ in range(5)]
        events = [_make_event(eid, data={"seq": i}) for i, eid in enumerate(event_ids)]

        with patch(
            "orxtra.incoming._replay.replay",
            new_callable=AsyncMock,
            return_value=events,
        ):
            app = _make_app(dispatch_backend, authenticator)
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    f"/incoming/events/{SLUG}/replay",
                    headers=_auth_headers(),
                )

            assert resp.status_code == 200
            data = resp.json()
            assert len(data) == 5
            returned_ids = [e["id"] for e in data]
            expected_ids = [str(eid) for eid in event_ids]
            assert returned_ids == expected_ids


class TestReplayRespectsSinceCursor:
    """Replay passes 'since' query param as since_id to trace.replay()."""

    async def test_since_cursor_passed(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        cursor_id = uuid6.uuid7()
        remaining_events = [_make_event(uuid6.uuid7())]

        with patch(
            "orxtra.incoming._replay.replay",
            new_callable=AsyncMock,
            return_value=remaining_events,
        ) as mock_replay:
            app = _make_app(dispatch_backend, authenticator)
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    f"/incoming/events/{SLUG}/replay?since={cursor_id}",
                    headers=_auth_headers(),
                )

            assert resp.status_code == 200
            mock_replay.assert_called_once()
            call_kwargs = mock_replay.call_args[1]
            assert call_kwargs["since_id"] == cursor_id
            assert call_kwargs["source"] == SLUG

    async def test_invalid_since_returns_400(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                f"/incoming/events/{SLUG}/replay?since=not-a-uuid",
                headers=_auth_headers(),
            )

        assert resp.status_code == 400
        assert "Invalid" in resp.text


class TestReplayRespectsLimit:
    """Replay passes 'limit' query param to trace.replay()."""

    async def test_custom_limit(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        with patch(
            "orxtra.incoming._replay.replay",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_replay:
            app = _make_app(dispatch_backend, authenticator)
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    f"/incoming/events/{SLUG}/replay?limit=50",
                    headers=_auth_headers(),
                )

            assert resp.status_code == 200
            mock_replay.assert_called_once()
            assert mock_replay.call_args[1]["limit"] == 50

    async def test_default_limit_is_100(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        with patch(
            "orxtra.incoming._replay.replay",
            new_callable=AsyncMock,
            return_value=[],
        ) as mock_replay:
            app = _make_app(dispatch_backend, authenticator)
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    f"/incoming/events/{SLUG}/replay",
                    headers=_auth_headers(),
                )

            assert resp.status_code == 200
            mock_replay.assert_called_once()
            assert mock_replay.call_args[1]["limit"] == 100


class TestReplayAuth:
    """Replay endpoint requires authentication."""

    async def test_missing_auth_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                f"/incoming/events/{SLUG}/replay",
            )

        assert resp.status_code == 401

    async def test_wrong_token_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                f"/incoming/events/{SLUG}/replay",
                headers={"Authorization": "Bearer wrong-token"},
            )

        assert resp.status_code == 401

    async def test_unknown_slug_returns_404(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/incoming/events/nonexistent/replay",
                headers=_auth_headers(),
            )

        assert resp.status_code == 404

    async def test_null_credential_returns_403(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_no_credential: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/incoming/events/no-cred-source/replay",
                headers=_auth_headers(),
            )

        assert resp.status_code == 403


class TestReplayEmptyResult:
    """Replay returns empty array when no events match."""

    async def test_empty_array(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        with patch(
            "orxtra.incoming._replay.replay",
            new_callable=AsyncMock,
            return_value=[],
        ):
            app = _make_app(dispatch_backend, authenticator)
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    f"/incoming/events/{SLUG}/replay",
                    headers=_auth_headers(),
                )

            assert resp.status_code == 200
            assert resp.json() == []


# ---------------------------------------------------------------------------
# SSE stream: HTTP-level auth tests
# ---------------------------------------------------------------------------


class TestSSEStreamAuth:
    """SSE stream endpoint requires authentication."""

    async def test_missing_auth_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        event_bus: InMemoryEventBus,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator, event_bus)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                f"/incoming/events/{SLUG}/stream",
            )

        assert resp.status_code == 401

    async def test_wrong_token_returns_401(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        event_bus: InMemoryEventBus,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator, event_bus)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                f"/incoming/events/{SLUG}/stream",
                headers={"Authorization": "Bearer wrong-token"},
            )

        assert resp.status_code == 401

    async def test_unknown_slug_returns_404(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        event_bus: InMemoryEventBus,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator, event_bus)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                "/incoming/events/nonexistent/stream",
                headers=_auth_headers(),
            )

        assert resp.status_code == 404


class TestSSEStreamNotRegisteredWithoutEventBus:
    """When event_bus is not provided, SSE stream route is not registered."""

    async def test_no_stream_route_without_event_bus(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
    ) -> None:
        app = _make_app(dispatch_backend, authenticator, event_bus=None)
        async with AsyncTestClient(app) as client:
            resp = await client.get(
                f"/incoming/events/{SLUG}/stream",
                headers=_auth_headers(),
            )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# SSE generator tests (direct, no HTTP layer)
#
# These test _sse_generator directly to avoid deadlocks from
# httpx ASGITransport + infinite async generators on the same event loop.
# ---------------------------------------------------------------------------


async def _collect_from_generator(
    gen: Any,
    max_events: int,
    collect_timeout: float = 2.0,
) -> list[str]:
    """Collect up to max_events SSE messages from the generator.

    Returns the raw SSE strings yielded by the generator.
    """
    collected: list[str] = []
    try:
        async with asyncio.timeout(collect_timeout):
            async for sse_msg in gen:
                # Skip heartbeats.
                if sse_msg.startswith(": heartbeat"):
                    continue
                collected.append(sse_msg)
                if len(collected) >= max_events:
                    break
    except TimeoutError:
        pass
    return collected


def _parse_sse_data(sse_msg: str) -> dict[str, Any]:
    """Extract the JSON data payload from an SSE message string."""
    for line in sse_msg.split("\n"):
        if line.startswith("data: "):
            return json.loads(line[6:])
    msg = f"No data line found in SSE message: {sse_msg!r}"
    raise ValueError(msg)


# Patch target paths (keep lines short).
_REPLAY = "orxtra.incoming._stream.replay"
_READ_EVENT = "orxtra.incoming._stream.read_event"


class TestSSEGeneratorNoLoss:
    """No-loss: events fired during the connect window all arrive."""

    async def test_events_during_connect_window_arrive(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """Fire events after subscription and verify all arrive."""
        event_ids = [uuid6.uuid7() for _ in range(3)]
        full_events = {
            eid: _make_event(eid, data={"i": i})
            for i, eid in enumerate(event_ids)
        }

        mock_pool = AsyncMock()

        async def mock_read_event(
            pool: Any, event_id: UUID,
        ) -> dict[str, Any] | None:
            return full_events.get(event_id)

        with (
            patch(_REPLAY, new_callable=AsyncMock, return_value=[]),
            patch(_READ_EVENT, side_effect=mock_read_event),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=None,
            )

            # Start consuming in a task so publish can happen concurrently.
            collected: list[str] = []

            async def consume() -> None:
                nonlocal collected
                collected = await _collect_from_generator(gen, max_events=3)

            consumer_task = asyncio.create_task(consume())

            # Give the generator a moment to subscribe.
            await asyncio.sleep(0.01)

            # Publish events.
            for eid in event_ids:
                payload = json.dumps({
                    "event_id": str(eid),
                    "source": SLUG,
                    "event_type": "test.event",
                })
                await event_bus.publish(EVENTS_CHANNEL, payload)

            await consumer_task

        assert len(collected) == 3
        received_ids = {_parse_sse_data(msg)["id"] for msg in collected}
        expected_ids = {str(eid) for eid in event_ids}
        assert received_ids == expected_ids


class TestSSEGeneratorLastEventIDResume:
    """Last-Event-ID resume: catch-up events arrive first."""

    async def test_resume_sends_missed_events(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        last_seen_id = uuid6.uuid7()
        missed_ids = [uuid6.uuid7(), uuid6.uuid7()]
        missed_events = [
            _make_event(eid, data={"missed": i})
            for i, eid in enumerate(missed_ids)
        ]

        mock_pool = AsyncMock()

        with (
            patch(
                _REPLAY,
                new_callable=AsyncMock,
                return_value=missed_events,
            ) as mock_replay,
            patch(_READ_EVENT, new_callable=AsyncMock, return_value=None),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=last_seen_id,
            )

            collected = await _collect_from_generator(gen, max_events=2)

        # Verify replay was called with the correct cursor.
        mock_replay.assert_called_once()
        call_kwargs = mock_replay.call_args[1]
        assert call_kwargs["since_id"] == last_seen_id
        assert call_kwargs["source"] == SLUG

        # Verify exactly the missed events arrived.
        assert len(collected) == 2
        received_ids = {_parse_sse_data(msg)["id"] for msg in collected}
        expected_ids = {str(eid) for eid in missed_ids}
        assert received_ids == expected_ids

    async def test_resume_no_gaps(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """After catch-up, live events also arrive -- no gap."""
        last_seen_id = uuid6.uuid7()
        missed_id = uuid6.uuid7()
        live_id = uuid6.uuid7()

        missed_event = _make_event(missed_id, data={"source": "catchup"})
        live_event = _make_event(live_id, data={"source": "live"})

        mock_pool = AsyncMock()

        async def mock_read_event(
            pool: Any, event_id: UUID,
        ) -> dict[str, Any] | None:
            if event_id == live_id:
                return live_event
            return None

        with (
            patch(
                _REPLAY,
                new_callable=AsyncMock,
                return_value=[missed_event],
            ),
            patch(_READ_EVENT, side_effect=mock_read_event),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=last_seen_id,
            )

            collected: list[str] = []

            async def consume() -> None:
                nonlocal collected
                collected = await _collect_from_generator(gen, max_events=2)

            consumer_task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            # Publish a live event after catch-up.
            payload = json.dumps({
                "event_id": str(live_id),
                "source": SLUG,
                "event_type": "test.event",
            })
            await event_bus.publish(EVENTS_CHANNEL, payload)

            await consumer_task

        # Should have catch-up + live = 2 events.
        assert len(collected) == 2
        received_ids = [_parse_sse_data(msg)["id"] for msg in collected]
        assert str(missed_id) in received_ids
        assert str(live_id) in received_ids


class TestSSEGeneratorDeduplication:
    """Events in both catch-up and live stream are deduplicated."""

    async def test_overlap_events_sent_once(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """An event appearing in both replay and NOTIFY is sent only once."""
        overlap_id = uuid6.uuid7()
        unique_live_id = uuid6.uuid7()

        catchup_event = _make_event(overlap_id, data={"from": "catchup"})

        live_events = {
            overlap_id: _make_event(overlap_id, data={"from": "live-overlap"}),
            unique_live_id: _make_event(unique_live_id, data={"from": "live-unique"}),
        }

        mock_pool = AsyncMock()

        async def mock_read_event(
            pool: Any, event_id: UUID,
        ) -> dict[str, Any] | None:
            return live_events.get(event_id)

        with (
            patch(
                _REPLAY,
                new_callable=AsyncMock,
                return_value=[catchup_event],
            ),
            patch(_READ_EVENT, side_effect=mock_read_event),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=uuid6.uuid7(),
            )

            collected: list[str] = []

            async def consume() -> None:
                nonlocal collected
                collected = await _collect_from_generator(gen, max_events=2)

            consumer_task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            # Publish both the overlap and the unique event.
            for eid in [overlap_id, unique_live_id]:
                payload = json.dumps({
                    "event_id": str(eid),
                    "source": SLUG,
                    "event_type": "test.event",
                })
                await event_bus.publish(EVENTS_CHANNEL, payload)

            await consumer_task

        # Should have exactly 2: catchup + unique live.
        # The overlap event should NOT appear twice.
        assert len(collected) == 2
        received_ids = [_parse_sse_data(msg)["id"] for msg in collected]
        assert str(overlap_id) in received_ids
        assert str(unique_live_id) in received_ids
        # No duplicates.
        assert len(set(received_ids)) == 2


class TestSSEGeneratorSourceFilter:
    """SSE stream only delivers events for the requested source slug."""

    async def test_other_source_events_filtered(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        our_event_id = uuid6.uuid7()
        other_event_id = uuid6.uuid7()

        our_event = _make_event(our_event_id, source=SLUG)

        mock_pool = AsyncMock()

        async def mock_read_event(
            pool: Any, eid: UUID,
        ) -> dict[str, Any] | None:
            if eid == our_event_id:
                return our_event
            return None

        with (
            patch(_REPLAY, new_callable=AsyncMock, return_value=[]),
            patch(_READ_EVENT, side_effect=mock_read_event),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=None,
            )

            collected: list[str] = []

            async def consume() -> None:
                nonlocal collected
                collected = await _collect_from_generator(gen, max_events=1)

            consumer_task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            # Publish from another source -- should be filtered.
            other_payload = json.dumps({
                "event_id": str(other_event_id),
                "source": "other-source",
                "event_type": "test.event",
            })
            await event_bus.publish(EVENTS_CHANNEL, other_payload)

            # Publish from our source.
            our_payload = json.dumps({
                "event_id": str(our_event_id),
                "source": SLUG,
                "event_type": "test.event",
            })
            await event_bus.publish(EVENTS_CHANNEL, our_payload)

            await consumer_task

        assert len(collected) == 1
        assert _parse_sse_data(collected[0])["id"] == str(our_event_id)


class TestSSEEventFormat:
    """SSE events use the correct wire format."""

    def test_format_sse_event_structure(self) -> None:
        """_format_sse_event produces id:, event:, data: lines."""
        event_id = uuid6.uuid7()
        event = _make_event(event_id, event_type="push")

        sse = _format_sse_event(event)

        lines = sse.split("\n")
        assert lines[0] == f"id: {event_id}"
        assert lines[1] == "event: push"
        assert lines[2].startswith("data: ")

        # Data is valid JSON containing the full event.
        data = json.loads(lines[2][6:])
        assert data["id"] == str(event_id)
        assert data["event_type"] == "push"

    def test_format_ends_with_double_newline(self) -> None:
        """SSE messages must end with \\n\\n."""
        event = _make_event(uuid6.uuid7())
        sse = _format_sse_event(event)
        assert sse.endswith("\n\n")

    def test_format_serializes_uuids_and_datetimes(self) -> None:
        """UUIDs and datetimes are converted to strings in JSON output."""
        event_id = uuid6.uuid7()
        event = _make_event(event_id)

        sse = _format_sse_event(event)
        data = json.loads(sse.split("\n")[2][6:])

        # UUID should be a string.
        assert isinstance(data["id"], str)
        assert data["id"] == str(event_id)

        # created_at should be an ISO string.
        assert isinstance(data["created_at"], str)
        assert "T" in data["created_at"]


class TestSSEGeneratorFetchOnNotify:
    """NOTIFY payload lacks data; generator fetches full event from DB."""

    async def test_full_event_fetched_on_notify(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """The NOTIFY payload has event_id but no data field.

        The generator must call read_event() to get the full event.
        """
        event_id = uuid6.uuid7()
        full_event = _make_event(
            event_id,
            event_type="webhook.received",
            data={"full": True, "payload": "complete"},
        )

        mock_pool = AsyncMock()
        read_event_mock = AsyncMock(return_value=full_event)

        with (
            patch(_REPLAY, new_callable=AsyncMock, return_value=[]),
            patch(_READ_EVENT, read_event_mock),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=None,
            )

            collected: list[str] = []

            async def consume() -> None:
                nonlocal collected
                collected = await _collect_from_generator(gen, max_events=1)

            consumer_task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            # NOTIFY payload has event_id but no data.
            payload = json.dumps({
                "event_id": str(event_id),
                "source": SLUG,
                "event_type": "webhook.received",
            })
            await event_bus.publish(EVENTS_CHANNEL, payload)

            await consumer_task

        # read_event was called to fetch the full event.
        read_event_mock.assert_called_once_with(mock_pool, event_id)

        # The delivered SSE contains the full payload.
        assert len(collected) == 1
        data = _parse_sse_data(collected[0])
        assert data["data"] == {"full": True, "payload": "complete"}
        assert data["event_type"] == "webhook.received"


class TestSSEGeneratorHeartbeat:
    """Generator sends heartbeat comments when idle."""

    async def test_heartbeat_on_timeout(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """When no events arrive, the generator yields heartbeat comments."""
        mock_pool = AsyncMock()

        # Patch the timeout to be very short so we get a heartbeat quickly.
        with (
            patch(_REPLAY, new_callable=AsyncMock, return_value=[]),
            patch(_READ_EVENT, new_callable=AsyncMock),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=None,
            )

            # Collect raw output including heartbeats.
            heartbeats: list[str] = []
            try:
                # Short timeout so we don't wait long.
                # The generator has a 15s heartbeat interval;
                # we'll mock wait_for to timeout immediately.
                with patch(
                    "orxtra.incoming._stream.asyncio.wait_for",
                    side_effect=asyncio.TimeoutError,
                ):
                    async with asyncio.timeout(0.5):
                        async for msg in gen:
                            heartbeats.append(msg)
                            if len(heartbeats) >= 2:
                                break
            except TimeoutError:
                pass

        # Should have received heartbeat comments.
        assert len(heartbeats) >= 1
        for hb in heartbeats:
            assert hb == ": heartbeat\n\n"


class TestSSEGeneratorCleanup:
    """SSE generator unsubscribes from the event bus on disconnect."""

    async def test_cleanup_on_cancellation(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """When the generator is cancelled (client disconnect), the
        callback is unsubscribed from the event bus."""
        mock_pool = AsyncMock()

        with (
            patch(_REPLAY, new_callable=AsyncMock, return_value=[]),
            patch(_READ_EVENT, new_callable=AsyncMock),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=None,
            )

            # Start consuming so the generator subscribes.
            async def consume() -> None:
                async for _ in gen:
                    pass  # Will be cancelled

            task = asyncio.create_task(consume())
            await asyncio.sleep(0.01)

            # Verify the callback is subscribed.
            assert EVENTS_CHANNEL in event_bus._subscribers
            assert len(event_bus._subscribers[EVENTS_CHANNEL]) == 1

            # Cancel the task (simulating client disconnect).
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

            # After cleanup, no subscribers should remain.
            subs = event_bus._subscribers.get(EVENTS_CHANNEL, [])
            assert len(subs) == 0

    async def test_cleanup_on_break(
        self,
        event_bus: InMemoryEventBus,
    ) -> None:
        """When the generator consumer breaks, the callback is cleaned up."""
        event_id = uuid6.uuid7()
        full_event = _make_event(event_id)
        mock_pool = AsyncMock()

        async def mock_read_event(
            pool: Any, eid: UUID,
        ) -> dict[str, Any] | None:
            if eid == event_id:
                return full_event
            return None

        with (
            patch(_REPLAY, new_callable=AsyncMock, return_value=[]),
            patch(_READ_EVENT, side_effect=mock_read_event),
        ):
            gen = _sse_generator(
                pool=mock_pool,
                event_bus=event_bus,
                slug=SLUG,
                last_event_id=None,
            )

            # Consume one event then break.
            async def consume_one() -> None:
                async for _ in gen:
                    break
                # Explicitly close the generator to trigger finally.
                await gen.aclose()

            task = asyncio.create_task(consume_one())
            await asyncio.sleep(0.01)

            payload = json.dumps({
                "event_id": str(event_id),
                "source": SLUG,
                "event_type": "test.event",
            })
            await event_bus.publish(EVENTS_CHANNEL, payload)

            await task

        subs = event_bus._subscribers.get(EVENTS_CHANNEL, [])
        assert len(subs) == 0


# ---------------------------------------------------------------------------
# Phase 2.6: verified AuthContext is captured, not discarded
# ---------------------------------------------------------------------------


class TestReplayAuthContextCaptured:
    """Replay captures the verified AuthContext and holds it at the site."""

    async def test_verified_context_held_at_replay_site(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        source_with_bearer: Source,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sentinel = _sentinel_auth_context()

        with (
            patch.object(
                authenticator,
                "verify_by_credential_id",
                new=AsyncMock(return_value=sentinel),
            ) as spy_verify,
            patch(
                "orxtra.incoming._replay.replay",
                new_callable=AsyncMock,
                return_value=[],
            ),
            caplog.at_level(logging.INFO, logger="orxtra.incoming._replay"),
        ):
            app = _make_app(dispatch_backend, authenticator)
            async with AsyncTestClient(app) as client:
                resp = await client.get(
                    f"/incoming/events/{SLUG}/replay",
                    headers=_auth_headers(),
                )

        assert resp.status_code == 200
        spy_verify.assert_awaited_once()
        # The sentinel identity reached the replay site: held, not discarded.
        assert str(sentinel.consumer_id) in caplog.text


class TestStreamAuthContextCaptured:
    """Stream captures the verified AuthContext and holds it at the site."""

    async def test_verified_context_held_at_stream_site(
        self,
        dispatch_backend: InMemoryDispatchBackend,
        authenticator: Authenticator,
        event_bus: InMemoryEventBus,
        source_with_bearer: Source,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        sentinel = _sentinel_auth_context()
        mock_pool = AsyncMock()
        request = _FakeRequest(SLUG, _auth_headers())

        with (
            patch.object(
                authenticator,
                "verify_by_credential_id",
                new=AsyncMock(return_value=sentinel),
            ) as spy_verify,
            caplog.at_level(logging.INFO, logger="orxtra.incoming._stream"),
        ):
            response = await stream_handler(
                request,
                pool=mock_pool,
                dispatch_backend=dispatch_backend,
                authenticator=authenticator,
                event_bus=event_bus,
            )

        # A successful connect returns a StreamResponse (not a 4xx text body).
        assert response.__class__.__name__ == "StreamResponse"
        spy_verify.assert_awaited_once()
        # The sentinel identity reached the stream site: held, not discarded.
        assert str(sentinel.consumer_id) in caplog.text
