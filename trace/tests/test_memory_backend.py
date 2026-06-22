"""Tests for InMemoryBackend and InMemoryEventBus."""

from __future__ import annotations

import asyncio
import inspect
from decimal import Decimal
from typing import Any

import pytest
from orxtra.trace._memory_backend import InMemoryBackend, InMemoryEventBus
from orxtra.trace._protocols import (
    EventBus,
    EventStorage,
    InboxStorage,
    NotepadStorage,
    OverseerStorage,
    RecoveryOperations,
    RunControlStorage,
    RunStorage,
    StorageBackend,
    StorageLock,
    StorageReader,
    TaskStorage,
)
from orxtra.trace._transitions import InvalidTransitionError

ALL_SUB_PROTOCOLS = [
    TaskStorage,
    EventStorage,
    RunStorage,
    RunControlStorage,
    OverseerStorage,
    InboxStorage,
    NotepadStorage,
    StorageReader,
    StorageLock,
    RecoveryOperations,
]


def _get_protocol_method_names(protocol: type) -> set[str]:
    """Get all method names declared in a Protocol class (excluding dunder)."""
    names: set[str] = set()
    for klass in protocol.__mro__:
        if klass is object:
            continue
        for name in klass.__dict__:
            if name.startswith("_"):
                continue
            obj = klass.__dict__[name]
            if callable(obj) or isinstance(obj, (classmethod, staticmethod)):
                names.add(name)
    return names


@pytest.fixture
def backend() -> InMemoryBackend:
    return InMemoryBackend()


@pytest.fixture
def event_bus() -> InMemoryEventBus:
    return InMemoryEventBus()


# ── Protocol conformance ──


class TestInMemoryBackendConformance:
    """Verify InMemoryBackend has every method from every sub-protocol."""

    @pytest.mark.parametrize("protocol", ALL_SUB_PROTOCOLS, ids=lambda p: p.__name__)
    def test_sub_protocol_methods_present(self, protocol: type) -> None:
        protocol_methods = _get_protocol_method_names(protocol)
        backend_methods = {
            name for name in dir(InMemoryBackend) if not name.startswith("_")
        }
        missing = protocol_methods - backend_methods
        assert not missing, (
            f"InMemoryBackend is missing methods from {protocol.__name__}: {sorted(missing)}"
        )

    def test_combined_storage_backend_methods(self) -> None:
        methods = _get_protocol_method_names(StorageBackend)
        backend_methods = {
            name for name in dir(InMemoryBackend) if not name.startswith("_")
        }
        missing = methods - backend_methods
        assert not missing, (
            f"InMemoryBackend is missing StorageBackend methods: {sorted(missing)}"
        )

    @pytest.mark.parametrize("protocol", ALL_SUB_PROTOCOLS, ids=lambda p: p.__name__)
    def test_signature_parameters_match(self, protocol: type) -> None:
        protocol_methods = _get_protocol_method_names(protocol)
        mismatches: list[str] = []
        for method_name in sorted(protocol_methods):
            proto_method = getattr(protocol, method_name, None)
            backend_method = getattr(InMemoryBackend, method_name, None)
            if proto_method is None or backend_method is None:
                continue
            proto_sig = inspect.signature(proto_method)
            backend_sig = inspect.signature(backend_method)
            proto_params = [p for p in proto_sig.parameters if p != "self"]
            backend_params = [p for p in backend_sig.parameters if p != "self"]
            if proto_params != backend_params:
                mismatches.append(
                    f"{method_name}: protocol={proto_params}, backend={backend_params}"
                )
        assert not mismatches, (
            f"Signature mismatches in {protocol.__name__}:\n" + "\n".join(mismatches)
        )


class TestInMemoryBackendRuntimeCheckable:
    """Verify runtime isinstance checks work."""

    def test_is_task_storage(self) -> None:
        assert isinstance(InMemoryBackend(), TaskStorage)

    def test_is_event_storage(self) -> None:
        assert isinstance(InMemoryBackend(), EventStorage)

    def test_is_run_storage(self) -> None:
        assert isinstance(InMemoryBackend(), RunStorage)

    def test_is_run_control_storage(self) -> None:
        assert isinstance(InMemoryBackend(), RunControlStorage)

    def test_is_overseer_storage(self) -> None:
        assert isinstance(InMemoryBackend(), OverseerStorage)

    def test_is_inbox_storage(self) -> None:
        assert isinstance(InMemoryBackend(), InboxStorage)

    def test_is_notepad_storage(self) -> None:
        assert isinstance(InMemoryBackend(), NotepadStorage)

    def test_is_storage_reader(self) -> None:
        assert isinstance(InMemoryBackend(), StorageReader)

    def test_is_storage_lock(self) -> None:
        assert isinstance(InMemoryBackend(), StorageLock)

    def test_is_recovery_operations(self) -> None:
        assert isinstance(InMemoryBackend(), RecoveryOperations)

    def test_is_storage_backend(self) -> None:
        assert isinstance(InMemoryBackend(), StorageBackend)

    def test_event_bus_is_event_bus(self) -> None:
        assert isinstance(InMemoryEventBus(), EventBus)


# ── Run CRUD ──


class TestRunOperations:
    @pytest.mark.asyncio
    async def test_create_run(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test intent", {"key": "val"}, "max")
        assert run_id is not None
        runs = await backend.list_runs()
        assert len(runs) == 1
        assert runs[0].intent == "test intent"
        assert runs[0].status == "created"

    @pytest.mark.asyncio
    async def test_run_transitions(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.transition_run(run_id, "running")
        runs = await backend.list_runs()
        assert runs[0].status == "running"
        await backend.transition_run(run_id, "completed")
        runs = await backend.list_runs()
        assert runs[0].status == "completed"
        assert runs[0].finished_at is not None

    @pytest.mark.asyncio
    async def test_invalid_run_transition(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        with pytest.raises(InvalidTransitionError):
            await backend.transition_run(run_id, "completed")

    @pytest.mark.asyncio
    async def test_run_terminal_state_transition(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.transition_run(run_id, "running")
        await backend.transition_run(run_id, "completed")
        with pytest.raises(InvalidTransitionError):
            await backend.transition_run(run_id, "running")

    @pytest.mark.asyncio
    async def test_read_run_report(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test intent", {"k": "v"}, "max")
        report = await backend.read_run_report(run_id)
        assert report is not None
        assert report.intent == "test intent"
        assert report.config_snapshot == {"k": "v"}

    @pytest.mark.asyncio
    async def test_read_run_config(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("intent", {"foo": "bar"}, "supervised")
        config = await backend.read_run_config(run_id)
        assert config == {"foo": "bar"}

    @pytest.mark.asyncio
    async def test_read_run_config_not_found(self, backend: InMemoryBackend) -> None:
        import uuid
        result = await backend.read_run_config(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_coherence_summary(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_coherence_summary(run_id, "All good")
        report = await backend.read_run_report(run_id)
        assert report is not None
        assert report.coherence_summary == "All good"


# ── Task CRUD ──


class TestTaskOperations:
    @pytest.mark.asyncio
    async def test_create_and_list_tasks(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        t1 = await backend.create_task(run_id, None, "task1", "agent")
        t2 = await backend.create_task(run_id, t1, "task2", "agent")
        tasks = await backend.list_tasks(run_id)
        assert len(tasks) == 2
        assert tasks[0].name == "task1"
        assert tasks[1].name == "task2"
        assert tasks[1].parent_task_id == t1

    @pytest.mark.asyncio
    async def test_task_transitions(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        await backend.transition_task(task_id, "prechecking")
        await backend.transition_task(task_id, "active")
        await backend.transition_task(task_id, "completed")
        tasks = await backend.list_tasks(run_id)
        assert tasks[0].status == "completed"

    @pytest.mark.asyncio
    async def test_invalid_task_transition(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        with pytest.raises(InvalidTransitionError):
            await backend.transition_task(task_id, "completed")

    @pytest.mark.asyncio
    async def test_task_cancelled_from_any_state(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        await backend.transition_task(task_id, "prechecking")
        await backend.transition_task(task_id, "cancelled")
        tasks = await backend.list_tasks(run_id)
        assert tasks[0].status == "cancelled"

    @pytest.mark.asyncio
    async def test_task_terminal_state_rejects_transition(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        await backend.transition_task(task_id, "prechecking")
        await backend.transition_task(task_id, "active")
        await backend.transition_task(task_id, "completed")
        with pytest.raises(InvalidTransitionError):
            await backend.transition_task(task_id, "active")


# ── Task attempt CRUD ──


class TestTaskAttemptOperations:
    @pytest.mark.asyncio
    async def test_create_and_complete_attempt(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        attempt_id = await backend.create_task_attempt(task_id, 1)
        await backend.complete_task_attempt(
            attempt_id=attempt_id,
            agent_output="done",
            structured_output={"result": "ok"},
            check_result=None,
            check_verdict=None,
            session_id=None,
            input_tokens=100,
            output_tokens=50,
            reasoning_tokens=10,
            cache_read_tokens=5,
            cache_write_tokens=3,
            cost_usd=Decimal("0.01"),
            duration_seconds=1.5,
        )
        att = await backend.read_task_attempt(task_id, 1)
        assert att is not None
        assert att.status == "completed"
        assert att.agent_output == "done"
        assert att.input_tokens == 100

    @pytest.mark.asyncio
    async def test_fail_attempt(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        attempt_id = await backend.create_task_attempt(task_id, 1)
        await backend.fail_task_attempt(
            attempt_id=attempt_id,
            error="something broke",
            session_id=None,
            input_tokens=50,
            output_tokens=10,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=Decimal("0.005"),
            duration_seconds=0.5,
        )
        att = await backend.read_task_attempt(task_id, 1)
        assert att is not None
        assert att.status == "failed"
        assert att.agent_output == "something broke"

    @pytest.mark.asyncio
    async def test_read_latest_attempt(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        await backend.create_task_attempt(task_id, 1)
        await backend.create_task_attempt(task_id, 2)
        latest = await backend.read_latest_attempt(task_id)
        assert latest is not None
        assert latest.attempt == 2

    @pytest.mark.asyncio
    async def test_read_task_attempts(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        await backend.create_task_attempt(task_id, 1)
        await backend.create_task_attempt(task_id, 2)
        attempts = await backend.read_task_attempts(task_id)
        assert len(attempts) == 2
        assert attempts[0].attempt == 1
        assert attempts[1].attempt == 2

    @pytest.mark.asyncio
    async def test_read_latest_attempt_none(self, backend: InMemoryBackend) -> None:
        import uuid
        result = await backend.read_latest_attempt(uuid.uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_attempt_count_in_task_summary(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        await backend.create_task_attempt(task_id, 1)
        await backend.create_task_attempt(task_id, 2)
        tasks = await backend.list_tasks(run_id)
        assert tasks[0].attempt_count == 2


# ── Iteration CRUD ──


class TestIterationOperations:
    @pytest.mark.asyncio
    async def test_create_and_complete_iteration(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        it_id = await backend.create_iteration(task_id, 0, "item0")
        await backend.complete_iteration(it_id, "result0", {"key": "val"}, None)
        iterations = await backend.list_iterations(task_id)
        assert len(iterations) == 1
        assert iterations[0].status == "completed"
        assert iterations[0].output == "result0"

    @pytest.mark.asyncio
    async def test_fail_iteration(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        it_id = await backend.create_iteration(task_id, 0, "item0")
        await backend.fail_iteration(it_id, "oops")
        iterations = await backend.list_iterations(task_id)
        assert len(iterations) == 1
        assert iterations[0].status == "failed"
        assert iterations[0].output == "oops"


# ── Event CRUD ──


class TestEventOperations:
    @pytest.mark.asyncio
    async def test_write_and_query_events(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_event(run_id, "custom", {"key": "val"})
        await backend.write_event(run_id, "other", {"key2": "val2"})
        all_events = await backend.query_events(run_id)
        assert len(all_events) >= 2
        custom_events = await backend.query_events(run_id, event_type="custom")
        assert any(e["event_type"] == "custom" for e in custom_events)

    @pytest.mark.asyncio
    async def test_query_events_with_limit(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        for i in range(10):
            await backend.write_event(run_id, "test", {"i": i})
        events = await backend.query_events(run_id, limit=3)
        assert len(events) == 3


# ── Transcript CRUD ──


class TestTranscriptOperations:
    @pytest.mark.asyncio
    async def test_write_and_read_transcript(self, backend: InMemoryBackend) -> None:
        import uuid
        run_id = await backend.create_run("test", {}, "max")
        session_id = uuid.uuid4()
        await backend.write_transcript_entry(
            session_id, run_id, 1, "user", "hello",
        )
        await backend.write_transcript_entry(
            session_id, run_id, 2, "assistant", "hi there",
        )
        transcript = await backend.read_transcript(session_id)
        assert len(transcript) == 2
        assert transcript[0]["role"] == "user"
        assert transcript[1]["content"] == "hi there"

    @pytest.mark.asyncio
    async def test_search_transcript(self, backend: InMemoryBackend) -> None:
        import uuid
        run_id = await backend.create_run("test", {}, "max")
        session_id = uuid.uuid4()
        await backend.write_transcript_entry(
            session_id, run_id, 1, "user", "find the bug",
        )
        await backend.write_transcript_entry(
            session_id, run_id, 2, "assistant", "no bugs here",
        )
        results = await backend.search_transcript(session_id, "bug")
        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_session_token_counts(self, backend: InMemoryBackend) -> None:
        import uuid
        run_id = await backend.create_run("test", {}, "max")
        session_id = uuid.uuid4()
        await backend.write_transcript_entry(
            session_id, run_id, 1, "user", "hello",
            tokens={"input_tokens": 10, "output_tokens": 5},
        )
        await backend.write_transcript_entry(
            session_id, run_id, 2, "assistant", "hi",
        )
        counts = await backend.read_session_token_counts(session_id)
        assert len(counts) == 1
        assert counts[0]["tokens"]["input_tokens"] == 10

    @pytest.mark.asyncio
    async def test_session_turn_count(self, backend: InMemoryBackend) -> None:
        import uuid
        run_id = await backend.create_run("test", {}, "max")
        session_id = uuid.uuid4()
        await backend.write_transcript_entry(
            session_id, run_id, 1, "user", "hello",
        )
        await backend.write_transcript_entry(
            session_id, run_id, 2, "assistant", "hi",
        )
        count = await backend.read_session_turn_count(session_id)
        assert count == 2


# ── Notepad CRUD ──


class TestNotepadOperations:
    @pytest.mark.asyncio
    async def test_write_and_read_notepad(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_notepad_entry(
            run_id, "task1", "agent1", "observation", "something happened",
        )
        entries = await backend.read_notepad(run_id)
        assert len(entries) == 1
        assert entries[0].text == "something happened"
        assert entries[0].task_name == "task1"


# ── Inbox CRUD ──


class TestInboxOperations:
    @pytest.mark.asyncio
    async def test_create_and_read_inbox(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        item_id = await backend.create_inbox_item(
            run_id=run_id,
            decision_type="choice",
            question="which path?",
            options=[{"label": "A"}, {"label": "B"}],
            assumed_option="A",
            work_proceeding="proceeding with A",
            contradiction_impact="minor",
        )
        items = await backend.read_inbox(run_id)
        assert len(items) == 1
        assert items[0].question == "which path?"
        assert items[0].status == "pending"

        item = await backend.read_inbox_item(item_id)
        assert item is not None
        assert item.assumed_option == "A"

    @pytest.mark.asyncio
    async def test_answer_inbox_item(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        item_id = await backend.create_inbox_item(
            run_id, "choice", "q?", [{"label": "A"}], None, None, None,
        )
        await backend.answer_inbox_item(item_id, "A")
        item = await backend.read_inbox_item(item_id)
        assert item is not None
        assert item.status == "answered"
        assert item.answer == "A"

    @pytest.mark.asyncio
    async def test_skip_inbox_item(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        item_id = await backend.create_inbox_item(
            run_id, "choice", "q?", [], None, None, None,
        )
        await backend.skip_inbox_item(item_id)
        item = await backend.read_inbox_item(item_id)
        assert item is not None
        assert item.status == "skipped"

    @pytest.mark.asyncio
    async def test_reject_inbox_item(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        item_id = await backend.create_inbox_item(
            run_id, "choice", "q?", [], None, None, None,
        )
        await backend.reject_inbox_item(item_id, "bad question")
        item = await backend.read_inbox_item(item_id)
        assert item is not None
        assert item.status == "rejected"
        assert item.rejection_reason == "bad question"

    @pytest.mark.asyncio
    async def test_expire_inbox_item(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        item_id = await backend.create_inbox_item(
            run_id, "choice", "q?", [], None, None, None,
        )
        await backend.expire_inbox_item(item_id)
        item = await backend.read_inbox_item(item_id)
        assert item is not None
        assert item.status == "expired"

    @pytest.mark.asyncio
    async def test_answer_non_pending_raises(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        item_id = await backend.create_inbox_item(
            run_id, "choice", "q?", [], None, None, None,
        )
        await backend.skip_inbox_item(item_id)
        with pytest.raises(InvalidTransitionError):
            await backend.answer_inbox_item(item_id, "A")

    @pytest.mark.asyncio
    async def test_read_inbox_with_status_filter(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.create_inbox_item(
            run_id, "choice", "q1", [], None, None, None,
        )
        item2 = await backend.create_inbox_item(
            run_id, "choice", "q2", [], None, None, None,
        )
        await backend.answer_inbox_item(item2, "yes")
        pending = await backend.read_inbox(run_id, status="pending")
        assert len(pending) == 1
        answered = await backend.read_inbox(run_id, status="answered")
        assert len(answered) == 1


# ── Overseer storage ──


class TestOverseerStorage:
    @pytest.mark.asyncio
    async def test_decisions(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_decision(run_id, "strategy", "approach_a", "faster")
        decisions = await backend.read_decisions(run_id)
        assert len(decisions) == 1
        assert decisions[0]["choice"] == "approach_a"

    @pytest.mark.asyncio
    async def test_constraints(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_constraint(run_id, "no writes", "hard", "user")
        constraints = await backend.read_constraints(run_id)
        assert len(constraints) == 1
        assert constraints[0]["text"] == "no writes"

    @pytest.mark.asyncio
    async def test_assumptions(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_assumption(run_id, "db is up", "task")
        assumptions = await backend.read_assumptions(run_id)
        assert len(assumptions) == 1
        assert assumptions[0]["text"] == "db is up"

    @pytest.mark.asyncio
    async def test_lessons(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_lesson(run_id, "always retry", ["retry"], True)
        lessons = await backend.query_lessons(run_id=run_id)
        assert len(lessons) == 1
        assert lessons[0]["text"] == "always retry"

    @pytest.mark.asyncio
    async def test_query_relevant_lessons(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_lesson(run_id, "lesson1", ["tag_a", "tag_b"], True)
        await backend.write_lesson(run_id, "lesson2", ["tag_c"], False)
        results = await backend.query_relevant_lessons(["tag_a"])
        assert len(results) == 1
        assert results[0]["text"] == "lesson1"

    @pytest.mark.asyncio
    async def test_query_lessons_permanent_only(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_lesson(run_id, "perm", ["x"], True)
        await backend.write_lesson(run_id, "temp", ["x"], False)
        permanent = await backend.query_lessons(permanent_only=True)
        assert len(permanent) == 1
        assert permanent[0]["text"] == "perm"

    @pytest.mark.asyncio
    async def test_workflow_status(self, backend: InMemoryBackend) -> None:
        import uuid
        wf_id = uuid.uuid4()
        await backend.update_workflow_status(wf_id, "step2", "healthy")
        status = await backend.read_workflow_status(wf_id)
        assert status is not None
        assert status["current_step"] == "step2"
        assert status["health"] == "healthy"

    @pytest.mark.asyncio
    async def test_context_diff(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        attempt_id = await backend.create_task_attempt(task_id, 1)
        await backend.write_context_diff(attempt_id, "before", "diff here")
        assert len(backend._context_diffs) == 1

    @pytest.mark.asyncio
    async def test_active_constraints(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_constraint(run_id, "c1", "hard", "user")
        active = await backend.read_active_constraints(run_id)
        assert len(active) == 1


# ── Lock operations ──


class TestLockOperations:
    @pytest.mark.asyncio
    async def test_acquire_and_release_lock(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.acquire_run_lock(run_id)
        assert backend._run_locks[run_id] is True
        await backend.release_run_lock(run_id)
        assert run_id not in backend._run_locks

    @pytest.mark.asyncio
    async def test_acquire_already_locked_raises(
        self, backend: InMemoryBackend,
    ) -> None:
        from orxtra.trace._lock import RunLockError
        run_id = await backend.create_run("test", {}, "max")
        await backend.acquire_run_lock(run_id)
        with pytest.raises(RunLockError):
            await backend.acquire_run_lock(run_id)

    @pytest.mark.asyncio
    async def test_heartbeat_and_stale(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        # No heartbeat yet -- should be stale
        assert await backend.is_lock_stale(run_id) is True
        await backend.update_heartbeat(run_id)
        # Fresh heartbeat -- not stale (threshold is 300s)
        assert await backend.is_lock_stale(run_id) is False


# ── Recovery operations ──


class TestRecoveryOperations:
    @pytest.mark.asyncio
    async def test_reclaim_interrupted(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        # Force task to active state
        backend._tasks[task_id]["status"] = "active"
        reclaimed = await backend.reclaim_interrupted()
        assert reclaimed == 1
        assert backend._tasks[task_id]["status"] == "cancelled"

    @pytest.mark.asyncio
    async def test_reevaluate_blocked(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        task_id = await backend.create_task(run_id, None, "task1", "agent")
        # Task is in "created" state with no parent
        results = await backend.reevaluate_blocked()
        assert task_id in results

    @pytest.mark.asyncio
    async def test_clean_orphaned(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        # Force run to running without a lock
        backend._runs[run_id]["status"] = "running"
        cleaned = await backend.clean_orphaned()
        assert cleaned == 1
        assert backend._runs[run_id]["status"] == "failed"

    @pytest.mark.asyncio
    async def test_clean_orphaned_skips_locked(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        backend._runs[run_id]["status"] = "running"
        await backend.acquire_run_lock(run_id)
        cleaned = await backend.clean_orphaned()
        assert cleaned == 0
        assert backend._runs[run_id]["status"] == "running"


# ── Run control subscription ──


class TestRunControl:
    @pytest.mark.asyncio
    async def test_subscribe_fires_on_already_paused(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        await backend.transition_run(run_id, "running")
        await backend.transition_run(run_id, "paused")
        signals: list[tuple[Any, str]] = []

        async def cb(rid: Any, status: str) -> None:
            signals.append((rid, status))

        await backend.subscribe_run_control(run_id, cb)
        assert len(signals) == 1
        assert signals[0][1] == "paused"

    @pytest.mark.asyncio
    async def test_subscribe_fires_on_transition(
        self, backend: InMemoryBackend,
    ) -> None:
        run_id = await backend.create_run("test", {}, "max")
        signals: list[tuple[Any, str]] = []

        async def cb(rid: Any, status: str) -> None:
            signals.append((rid, status))

        await backend.subscribe_run_control(run_id, cb)
        await backend.transition_run(run_id, "running")
        assert len(signals) == 1
        assert signals[0][1] == "running"

    @pytest.mark.asyncio
    async def test_unsubscribe(self, backend: InMemoryBackend) -> None:
        run_id = await backend.create_run("test", {}, "max")
        signals: list[str] = []

        async def cb(rid: Any, status: str) -> None:
            signals.append(status)

        await backend.subscribe_run_control(run_id, cb)
        await backend.unsubscribe_run_control(run_id)
        await backend.transition_run(run_id, "running")
        assert len(signals) == 0


# ── EventBus ──


class TestInMemoryEventBus:
    @pytest.mark.asyncio
    async def test_subscribe_and_publish(self, event_bus: InMemoryEventBus) -> None:
        received: list[str] = []

        async def handler(payload: str) -> None:
            received.append(payload)

        await event_bus.subscribe("test_channel", handler)
        await event_bus.publish("test_channel", "hello")
        assert received == ["hello"]

    @pytest.mark.asyncio
    async def test_multiple_subscribers(self, event_bus: InMemoryEventBus) -> None:
        received: list[str] = []

        async def handler1(payload: str) -> None:
            received.append(f"h1:{payload}")

        async def handler2(payload: str) -> None:
            received.append(f"h2:{payload}")

        await event_bus.subscribe("ch", handler1)
        await event_bus.subscribe("ch", handler2)
        await event_bus.publish("ch", "msg")
        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_publish_to_unsubscribed_channel(
        self, event_bus: InMemoryEventBus,
    ) -> None:
        # Should not raise
        await event_bus.publish("nonexistent", "payload")

    @pytest.mark.asyncio
    async def test_is_event_bus_protocol(self) -> None:
        bus = InMemoryEventBus()
        assert isinstance(bus, EventBus)


# ── Event callback ──


class TestEventCallback:
    @pytest.mark.asyncio
    async def test_event_callback_fires_on_write_event(
        self, backend: InMemoryBackend,
    ) -> None:
        received: list[dict[str, Any]] = []

        async def cb(
            event_id: Any, run_id: Any, event_type: str, data: dict[str, Any],
        ) -> None:
            received.append({"event_type": event_type, "data": data})

        backend._event_callback = cb
        run_id = await backend.create_run("test", {}, "max")
        await backend.write_event(run_id, "custom", {"k": "v"})
        assert len(received) == 1
        assert received[0]["event_type"] == "custom"

    @pytest.mark.asyncio
    async def test_event_callback_fires_on_transition(
        self, backend: InMemoryBackend,
    ) -> None:
        received: list[str] = []

        async def cb(
            event_id: Any, run_id: Any, event_type: str, data: dict[str, Any],
        ) -> None:
            received.append(event_type)

        backend._event_callback = cb
        run_id = await backend.create_run("test", {}, "max")
        await backend.transition_run(run_id, "running")
        assert "run_transition" in received
