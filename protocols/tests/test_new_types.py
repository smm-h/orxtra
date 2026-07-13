from __future__ import annotations

from dataclasses import FrozenInstanceError
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import pytest
from orxtra.protocols import (
    VALID_INJECT_TOKENS,
    AuthContext,
    Capability,
    CardContributor,
    CreateSurface,
    DeleteSurface,
    EventSink,
    SurfaceGenerator,
    SurfaceSpec,
    TrustTier,
    UpdateComponents,
    UpdateDataModel,
)
from pydantic import BaseModel, ValidationError

# -- EventSink[T] --


class _StringSink:
    """Minimal EventSink[str] implementation."""

    def __init__(self) -> None:
        self.received: list[str] = []

    async def on_event(self, event: str) -> None:
        self.received.append(event)


class _IntSink:
    """Minimal EventSink[int] implementation."""

    async def on_event(self, event: int) -> None:
        pass


class TestEventSink:
    def test_runtime_checkable_string(self) -> None:
        sink = _StringSink()
        assert isinstance(sink, EventSink)

    def test_runtime_checkable_int(self) -> None:
        sink = _IntSink()
        assert isinstance(sink, EventSink)

    def test_non_conforming_rejected(self) -> None:
        assert not isinstance(object(), EventSink)

    def test_parameterized_type(self) -> None:
        # Verify EventSink can be parameterized without error.
        hint = EventSink[str]
        assert hint is not None


# -- TrustTier --


class TestTrustTier:
    def test_all_four_values(self) -> None:
        expected = {"anonymous", "identified", "verified", "system"}
        actual = {t.value for t in TrustTier}
        assert actual == expected
        assert len(TrustTier) == 4

    def test_string_comparison(self) -> None:
        assert TrustTier.ANONYMOUS == "anonymous"
        assert TrustTier.SYSTEM == "system"


# -- AuthContext --


class TestAuthContext:
    def test_construction_all_fields(self) -> None:
        now = datetime.now(tz=UTC)
        uid = uuid4()
        cid = uuid4()
        p = AuthContext(
            id=uid,
            consumer_id=cid,
            scopes=frozenset({"read", "write"}),
            trust_tier=TrustTier.VERIFIED,
            authenticated_via="oauth2",
            issued_at=now,
            expires_at=None,
        )
        assert p.id == uid
        assert p.consumer_id == cid
        assert p.scopes == frozenset({"read", "write"})
        assert p.trust_tier == TrustTier.VERIFIED
        assert p.authenticated_via == "oauth2"
        assert p.issued_at == now
        assert p.expires_at is None

    def test_construction_with_expiry(self) -> None:
        now = datetime.now(tz=UTC)
        later = datetime(2030, 1, 1, tzinfo=UTC)
        p = AuthContext(
            id=uuid4(),
            consumer_id=uuid4(),
            scopes=frozenset(),
            trust_tier=TrustTier.ANONYMOUS,
            authenticated_via="none",
            issued_at=now,
            expires_at=later,
        )
        assert p.expires_at == later

    def test_construction_system_tier_none_consumer(self) -> None:
        # consumer_id is None only for system-tier contexts, which have
        # no backing consumer record.
        now = datetime.now(tz=UTC)
        p = AuthContext(
            id=uuid4(),
            consumer_id=None,
            scopes=frozenset({"events:read"}),
            trust_tier=TrustTier.SYSTEM,
            authenticated_via="system",
            issued_at=now,
            expires_at=None,
        )
        assert p.consumer_id is None
        assert p.trust_tier == TrustTier.SYSTEM

    def test_frozen(self) -> None:
        p = AuthContext(
            id=uuid4(),
            consumer_id=uuid4(),
            scopes=frozenset(),
            trust_tier=TrustTier.IDENTIFIED,
            authenticated_via="api_key",
            issued_at=datetime.now(tz=UTC),
            expires_at=None,
        )
        with pytest.raises(FrozenInstanceError):
            p.trust_tier = TrustTier.SYSTEM  # type: ignore[misc]


# -- SurfaceOperation variants --


class TestCreateSurface:
    def test_valid_with_defaults(self) -> None:
        s = CreateSurface(surface_id="s1", catalog_id="c1")
        assert s.surface_id == "s1"
        assert s.catalog_id == "c1"
        assert s.theme is None
        assert s.send_data_model is False

    def test_with_theme(self) -> None:
        s = CreateSurface(
            surface_id="s1",
            catalog_id="c1",
            theme={"color": "blue"},
            send_data_model=True,
        )
        assert s.theme == {"color": "blue"}
        assert s.send_data_model is True

    def test_extra_field_rejected(self) -> None:
        with pytest.raises(ValidationError):
            CreateSurface(surface_id="s1", catalog_id="c1", extra="y")  # type: ignore[call-arg]

    def test_frozen(self) -> None:
        s = CreateSurface(surface_id="s1", catalog_id="c1")
        with pytest.raises(ValidationError):
            s.surface_id = "s2"  # type: ignore[misc]

    def test_json_round_trip(self) -> None:
        s = CreateSurface(
            surface_id="s1",
            catalog_id="c1",
            theme={"accent": "#ff0"},
            send_data_model=True,
        )
        data = s.model_dump_json()
        restored = CreateSurface.model_validate_json(data)
        assert restored == s


class TestUpdateComponents:
    def test_valid(self) -> None:
        u = UpdateComponents(
            surface_id="s1",
            components=[{"type": "button", "label": "Click"}],
        )
        assert u.surface_id == "s1"
        assert len(u.components) == 1

    def test_json_round_trip(self) -> None:
        u = UpdateComponents(
            surface_id="s1",
            components=[{"type": "text", "value": "hello"}],
        )
        data = u.model_dump_json()
        restored = UpdateComponents.model_validate_json(data)
        assert restored == u


class TestUpdateDataModel:
    def test_valid_with_defaults(self) -> None:
        u = UpdateDataModel(surface_id="s1")
        assert u.surface_id == "s1"
        assert u.path == "/"
        assert u.value is None

    def test_with_value(self) -> None:
        u = UpdateDataModel(surface_id="s1", path="/items", value=[1, 2, 3])
        assert u.path == "/items"
        assert u.value == [1, 2, 3]

    def test_json_round_trip(self) -> None:
        u = UpdateDataModel(surface_id="s1", path="/count", value=42)
        data = u.model_dump_json()
        restored = UpdateDataModel.model_validate_json(data)
        assert restored == u


class TestDeleteSurface:
    def test_valid(self) -> None:
        d = DeleteSurface(surface_id="s1")
        assert d.surface_id == "s1"

    def test_json_round_trip(self) -> None:
        d = DeleteSurface(surface_id="s1")
        data = d.model_dump_json()
        restored = DeleteSurface.model_validate_json(data)
        assert restored == d


# -- Capability --


class _StubParams(BaseModel):
    """Minimal BaseModel subclass for Capability tests."""


class TestCapability:
    def test_construction(self) -> None:
        c = Capability(
            name="read_file",
            namespace="fs",
            description="Read a file from disk",
            params_model=_StubParams,
            result_model=str,
            tags=frozenset({"io", "read"}),
            category="filesystem",
            required_scope="files:read",
            injects=frozenset({"pool"}),
        )
        assert c.name == "read_file"
        assert c.namespace == "fs"
        assert c.description == "Read a file from disk"
        assert c.params_model is _StubParams
        assert c.result_model is str
        assert c.tags == frozenset({"io", "read"})
        assert c.category == "filesystem"
        assert c.required_scope == "files:read"
        assert c.injects == frozenset({"pool"})

    def test_result_model_none(self) -> None:
        c = Capability(
            name="noop",
            namespace="test",
            description="Does nothing",
            params_model=_StubParams,
            result_model=None,
            tags=frozenset(),
            category="test",
            required_scope="test:read",
            injects=frozenset(),
        )
        assert c.result_model is None

    def test_frozen(self) -> None:
        c = Capability(
            name="x",
            namespace="n",
            description="d",
            params_model=_StubParams,
            result_model=None,
            tags=frozenset(),
            category="c",
            required_scope="x:read",
            injects=frozenset(),
        )
        with pytest.raises(FrozenInstanceError):
            c.name = "y"  # type: ignore[misc]

    def test_valid_inject_tokens(self) -> None:
        assert frozenset({
            "pool",
            "dispatch_backend",
            "principal_storage",
            "kind_registry",
            "caller_principal",
        }) == VALID_INJECT_TOKENS

    def test_unknown_inject_token_rejected(self) -> None:
        with pytest.raises(ValueError, match="unknown inject token"):
            Capability(
                name="bad",
                namespace="test",
                description="Declares a bogus inject token",
                params_model=_StubParams,
                result_model=None,
                tags=frozenset(),
                category="test",
                required_scope="test:read",
                injects=frozenset({"pool", "not_a_real_token"}),
            )


# -- SurfaceGenerator and CardContributor importability --


class _StubSurfaceGenerator:
    def generate(self, model_type: type) -> SurfaceSpec:
        return SurfaceSpec(
            surface_id="test",
            catalog_id="cat",
            theme=None,
        )


class _StubCardContributor:
    def card_fragment(self) -> dict[str, Any]:
        return {"name": "test-agent"}


class TestSurfaceGenerator:
    def test_runtime_checkable(self) -> None:
        gen = _StubSurfaceGenerator()
        assert isinstance(gen, SurfaceGenerator)

    def test_non_conforming_rejected(self) -> None:
        assert not isinstance(object(), SurfaceGenerator)


class TestCardContributor:
    def test_runtime_checkable(self) -> None:
        contrib = _StubCardContributor()
        assert isinstance(contrib, CardContributor)

    def test_non_conforming_rejected(self) -> None:
        assert not isinstance(object(), CardContributor)
