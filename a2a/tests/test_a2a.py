"""Tests for the orxtra A2A module."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import pytest

if TYPE_CHECKING:
    from pathlib import Path
from a2a.server.context import ServerCallContext
from a2a.types.a2a_pb2 import (
    TASK_STATE_CANCELED,
    TASK_STATE_COMPLETED,
    TASK_STATE_FAILED,
    TASK_STATE_SUBMITTED,
    TASK_STATE_WORKING,
    GetTaskRequest,
    Message,
    Part,
    SendMessageRequest,
)
from orxtra.a2a._agent_card import build_agent_card
from orxtra.a2a._server import (
    OrxtraRequestHandler,
    _OrxtraServerCallContextBuilder,
)
from orxtra.a2a._skills import (
    _EXCLUDED_NAMESPACES,
    SkillRegistry,
)
from orxtra.a2a._state_bridge import (
    TaskStateBridge,
    TranslationResult,
)
from orxtra.protocols import AuthContext, Capability, TaskState, TrustTier
from orxtra.services import DispatchContext
from pydantic import BaseModel
from starlette.requests import Request


class _EmptyParams(BaseModel):
    pass


def _make_capability(
    name: str,
    namespace: str = "test",
    description: str = "A test capability",
) -> Capability:
    """Create a minimal Capability for testing."""
    return Capability(
        name=name,
        namespace=namespace,
        description=description,
        params_model=_EmptyParams,
        result_model=None,
        tags=frozenset({"readonly"}),
        category="test",
        required_scope="test:read",
        injects=frozenset(),
    )


@pytest.fixture
def sample_capabilities() -> list[Capability]:
    return [
        _make_capability(
            "start_run",
            namespace="run",
            description="Start a run",
        ),
        _make_capability(
            "list_runs",
            namespace="run",
            description="List runs",
        ),
        _make_capability(
            "validate_agent",
            namespace="validate",
            description="Validate an agent",
        ),
        _make_capability(
            "subscribe",
            namespace="dispatch",
            description="Subscribe",
        ),
    ]


@pytest.fixture
def skill_registry(
    sample_capabilities: list[Capability],
) -> SkillRegistry:
    return SkillRegistry(sample_capabilities)


# -- Agent Card tests (7.1) --


class TestAgentCard:
    def test_card_has_skills(
        self, skill_registry: SkillRegistry,
    ) -> None:
        card = build_agent_card(
            skill_registry,
            url="http://localhost:8080/a2a",
            version="0.7.0",
        )
        assert card.name == "orxtra"
        assert card.version == "0.7.0"
        assert len(card.skills) > 0

    def test_card_skills_match_registry(
        self, skill_registry: SkillRegistry,
    ) -> None:
        card = build_agent_card(
            skill_registry,
            url="http://localhost:8080/a2a",
            version="0.7.0",
        )
        registry_skills = skill_registry.list_skills()
        assert len(card.skills) == len(registry_skills)

        card_ids = {s.id for s in card.skills}
        registry_ids = {s.id for s in registry_skills}
        assert card_ids == registry_ids

    def test_card_has_streaming_capability(
        self, skill_registry: SkillRegistry,
    ) -> None:
        card = build_agent_card(
            skill_registry,
            url="http://localhost:8080/a2a",
            version="0.7.0",
        )
        assert card.capabilities.streaming is True

    def test_card_has_interface_url(
        self, skill_registry: SkillRegistry,
    ) -> None:
        card = build_agent_card(
            skill_registry,
            url="http://localhost:9999/a2a",
            version="0.7.0",
        )
        assert len(card.supported_interfaces) == 1
        assert (
            card.supported_interfaces[0].url
            == "http://localhost:9999/a2a"
        )

    def test_card_custom_name_and_description(
        self, skill_registry: SkillRegistry,
    ) -> None:
        card = build_agent_card(
            skill_registry,
            url="http://localhost:8080/a2a",
            version="1.0.0",
            name="custom-agent",
            description="Custom description",
        )
        assert card.name == "custom-agent"
        assert card.description == "Custom description"
        assert card.version == "1.0.0"


# -- Skill Registry tests (7.2) --


class TestSkillRegistry:
    def test_auto_generates_from_capabilities(
        self, sample_capabilities: list[Capability],
    ) -> None:
        registry = SkillRegistry(sample_capabilities)
        skills = registry.list_skills()
        skill_ids = {s.id for s in skills}
        assert "start_run" in skill_ids
        assert "list_runs" in skill_ids
        assert "validate_agent" not in skill_ids
        assert "subscribe" not in skill_ids

    def test_excluded_namespaces(self) -> None:
        assert "validate" in _EXCLUDED_NAMESPACES
        assert "dispatch" in _EXCLUDED_NAMESPACES

    def test_skill_has_correct_modes(
        self, skill_registry: SkillRegistry,
    ) -> None:
        skills = skill_registry.list_skills()
        for skill in skills:
            assert skill.input_modes == (
                "text/plain",
                "application/json",
            )
            assert skill.output_modes == ("application/json",)

    def test_get_skill_by_id(
        self, skill_registry: SkillRegistry,
    ) -> None:
        skill = skill_registry.get_skill("start_run")
        assert skill is not None
        assert skill.capability_name == "start_run"

    def test_get_skill_not_found(
        self, skill_registry: SkillRegistry,
    ) -> None:
        skill = skill_registry.get_skill("nonexistent")
        assert skill is None

    def test_get_capability_for_skill(
        self, skill_registry: SkillRegistry,
    ) -> None:
        cap = skill_registry.get_capability_for_skill(
            "start_run",
        )
        assert cap is not None
        assert cap.name == "start_run"

    def test_get_capability_for_missing_skill(
        self, skill_registry: SkillRegistry,
    ) -> None:
        cap = skill_registry.get_capability_for_skill(
            "nonexistent",
        )
        assert cap is None

    def test_toml_loading_validates_capability(
        self, tmp_path: Path,
    ) -> None:
        skill_file = tmp_path / "bad_skill.toml"
        skill_file.write_text(
            'id = "bad"\n'
            'name = "Bad"\n'
            'description = "Bad skill"\n'
            'capability_name = "nonexistent_cap"\n'
        )
        caps = [_make_capability("real_cap")]
        with pytest.raises(
            ValueError, match="unknown capability",
        ):
            SkillRegistry(caps, skills_dir=tmp_path)

    def test_toml_loading_success(
        self, tmp_path: Path,
    ) -> None:
        skill_file = tmp_path / "my_skill.toml"
        skill_file.write_text(
            'id = "my_skill"\n'
            'name = "My Skill"\n'
            'description = "A skill from TOML"\n'
            'capability_name = "real_cap"\n'
        )
        caps = [_make_capability("real_cap")]
        registry = SkillRegistry(caps, skills_dir=tmp_path)
        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0].id == "my_skill"
        assert skills[0].description == "A skill from TOML"

    def test_empty_dir_falls_back_to_auto_generate(
        self, tmp_path: Path,
    ) -> None:
        caps = [_make_capability("cap_a", namespace="run")]
        registry = SkillRegistry(caps, skills_dir=tmp_path)
        skills = registry.list_skills()
        assert len(skills) == 1
        assert skills[0].id == "cap_a"


# -- Task State Bridge tests (7.3) --


class TestTaskStateBridge:
    @pytest.fixture
    def bridge(self) -> TaskStateBridge:
        return TaskStateBridge()

    def test_created_maps_to_submitted(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.CREATED)
        assert result.a2a_state == TASK_STATE_SUBMITTED

    def test_prechecking_maps_to_working(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.PRECHECKING)
        assert result.a2a_state == TASK_STATE_WORKING

    def test_active_maps_to_working(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.ACTIVE)
        assert result.a2a_state == TASK_STATE_WORKING

    def test_suspended_maps_to_working(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.SUSPENDED)
        assert result.a2a_state == TASK_STATE_WORKING

    def test_postchecking_maps_to_working(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.POSTCHECKING)
        assert result.a2a_state == TASK_STATE_WORKING

    def test_completed_maps_to_completed(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.COMPLETED)
        assert result.a2a_state == TASK_STATE_COMPLETED

    def test_precheck_failed_maps_to_failed(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.PRECHECK_FAILED)
        assert result.a2a_state == TASK_STATE_FAILED

    def test_postcheck_failed_is_buffered(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.POSTCHECK_FAILED)
        assert result.a2a_state is None

    def test_escalated_maps_to_failed(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.ESCALATED)
        assert result.a2a_state == TASK_STATE_FAILED

    def test_cancelled_maps_to_canceled(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.CANCELLED)
        assert result.a2a_state == TASK_STATE_CANCELED

    def test_all_task_states_covered(
        self, bridge: TaskStateBridge,
    ) -> None:
        """Every TaskState enum member must be handled."""
        for state in TaskState:
            result = bridge.translate(state)
            assert isinstance(result, TranslationResult)

    def test_extension_metadata_carries_sub_state(
        self, bridge: TaskStateBridge,
    ) -> None:
        result = bridge.translate(TaskState.ACTIVE)
        assert (
            result.extension_metadata["orxtra:sub_state"]
            == "active"
        )

        result = bridge.translate(TaskState.POSTCHECK_FAILED)
        assert (
            result.extension_metadata["orxtra:sub_state"]
            == "postcheck_failed"
        )

    def test_exhaustive_enum_coverage(self) -> None:
        """Verify the import-time exhaustive check."""
        from orxtra.a2a._state_bridge import _MAP

        for member in TaskState:
            assert member in _MAP, (
                f"TaskState.{member.name} not in _MAP"
            )


# -- Per-request identity tests (2.4) --


_SENTINEL_AUTH = AuthContext(
    id=UUID("11111111-1111-1111-1111-111111111111"),
    consumer_id=UUID("22222222-2222-2222-2222-222222222222"),
    scopes=frozenset({"runs:read", "runs:manage"}),
    trust_tier=TrustTier.VERIFIED,
    authenticated_via="bearer",
    issued_at=datetime(2026, 1, 1, tzinfo=UTC),
    expires_at=None,
)


class _Recorder:
    """Captures the DispatchContext handed to each dispatch call."""

    def __init__(self) -> None:
        self.contexts: list[DispatchContext] = []

    async def dispatch(
        self,
        context: DispatchContext,
        capability_name: str,
        params: dict[str, object],
    ) -> None:
        self.contexts.append(context)


def _build_call_context(
    auth_context: AuthContext | None,
) -> ServerCallContext:
    """Run a request scope through the real builder to get a ServerCallContext.

    When ``auth_context`` is None the scope carries an empty ``state`` (the
    open-mode shape), so the builder must resolve the absent key to None.
    """
    state: dict[str, object] = {}
    if auth_context is not None:
        state["auth_context"] = auth_context
    scope = {"type": "http", "headers": [], "state": state}
    request = Request(scope)
    return _OrxtraServerCallContextBuilder().build(request)


@pytest.fixture
def handler(skill_registry: SkillRegistry) -> OrxtraRequestHandler:
    return OrxtraRequestHandler(
        dispatch_context=DispatchContext(),
        skill_registry=skill_registry,
    )


class TestPerRequestIdentity:
    def test_builder_surfaces_auth_context_from_scope(self) -> None:
        context = _build_call_context(_SENTINEL_AUTH)
        assert context.state["auth_context"] is _SENTINEL_AUTH

    def test_builder_resolves_absent_key_to_none(self) -> None:
        context = _build_call_context(None)
        assert context.state["auth_context"] is None

    def test_builder_preserves_default_state(self) -> None:
        """The subclass keeps what the default builder provides (headers)."""
        context = _build_call_context(_SENTINEL_AUTH)
        assert "headers" in context.state

    async def test_message_send_threads_auth_context(
        self,
        handler: OrxtraRequestHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(
            "orxtra.services.dispatch", recorder.dispatch,
        )
        params = SendMessageRequest(
            message=Message(
                message_id="m1",
                parts=[Part(text="config.toml")],
            ),
        )
        await handler.on_message_send(
            params, _build_call_context(_SENTINEL_AUTH),
        )
        assert recorder.contexts[0].auth_context is _SENTINEL_AUTH

    async def test_read_handler_threads_auth_context(
        self,
        handler: OrxtraRequestHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(
            "orxtra.services.dispatch", recorder.dispatch,
        )
        await handler.on_get_task(
            GetTaskRequest(id="run-1"),
            _build_call_context(_SENTINEL_AUTH),
        )
        assert recorder.contexts[0].auth_context is _SENTINEL_AUTH

    async def test_absent_auth_context_yields_none(
        self,
        handler: OrxtraRequestHandler,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        recorder = _Recorder()
        monkeypatch.setattr(
            "orxtra.services.dispatch", recorder.dispatch,
        )
        await handler.on_message_send(
            SendMessageRequest(
                message=Message(
                    message_id="m2",
                    parts=[Part(text="config.toml")],
                ),
            ),
            _build_call_context(None),
        )
        assert recorder.contexts[0].auth_context is None
