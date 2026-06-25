"""Tests for action executor: ScriptAction, LogAction, WorkflowAction, EventAction, bounded concurrency."""

from __future__ import annotations

import logging
from typing import Any

import pytest

from _handlers import script_calls
from orxtra.dispatch import execute_action, execute_actions_bounded
from orxtra.protocols import EventAction, LogAction, ScriptAction, WorkflowAction


# -- Fixtures and helpers --


class FakeWorkflowExecutor:
    """Records execute_workflow calls."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object], list[dict[str, object]]]] = []

    async def execute_workflow(
        self,
        workflow_path: str,
        config: dict[str, object],
        events: list[dict[str, object]],
    ) -> None:
        self.calls.append((workflow_path, config, events))


@pytest.fixture(autouse=True)
def _clear_script_calls() -> None:
    script_calls.clear()


# -- ScriptAction tests --


class TestScriptAction:
    async def test_sync_callable(self) -> None:
        action = ScriptAction(
            callable="_handlers:sample_sync_handler",
        )
        events: list[dict[str, object]] = [{"key": "value"}]
        await execute_action(action, events)
        assert len(script_calls) == 1
        assert script_calls[0] == events

    async def test_async_callable(self) -> None:
        action = ScriptAction(
            callable="_handlers:sample_async_handler",
        )
        events: list[dict[str, object]] = [{"async": True}]
        await execute_action(action, events)
        assert len(script_calls) == 1
        assert script_calls[0] == events

    async def test_invalid_callable_format(self) -> None:
        action = ScriptAction(callable="no_colon_here")
        with pytest.raises(ValueError, match="Invalid callable path"):
            await execute_action(action, [])

    async def test_module_not_found(self) -> None:
        action = ScriptAction(callable="nonexistent.module:func")
        with pytest.raises(ImportError, match="Module not found"):
            await execute_action(action, [])

    async def test_function_not_found(self) -> None:
        action = ScriptAction(callable="_handlers:nonexistent_func")
        with pytest.raises(AttributeError, match="not found in module"):
            await execute_action(action, [])


# -- LogAction tests --


class TestLogAction:
    async def test_logs_at_info(self, caplog: pytest.LogCaptureFixture) -> None:
        action = LogAction(message="test log message", level="info")
        with caplog.at_level(logging.INFO, logger="orxtra.dispatch._action_executor"):
            await execute_action(action, [{"a": 1}, {"b": 2}])
        assert "test log message" in caplog.text
        assert "events=2" in caplog.text

    async def test_logs_at_warning(self, caplog: pytest.LogCaptureFixture) -> None:
        action = LogAction(message="warn msg", level="warning")
        with caplog.at_level(logging.WARNING, logger="orxtra.dispatch._action_executor"):
            await execute_action(action, [])
        assert "warn msg" in caplog.text

    async def test_logs_at_error(self, caplog: pytest.LogCaptureFixture) -> None:
        action = LogAction(message="error msg", level="error")
        with caplog.at_level(logging.ERROR, logger="orxtra.dispatch._action_executor"):
            await execute_action(action, [{"e": 1}])
        assert "error msg" in caplog.text


# -- WorkflowAction tests --


class TestWorkflowAction:
    async def test_calls_executor(self) -> None:
        executor = FakeWorkflowExecutor()
        action = WorkflowAction(
            workflow_path="workflows/deploy.toml",
            config={"env": "prod"},
        )
        events: list[dict[str, object]] = [{"task_id": "123"}]
        await execute_action(
            action, events, workflow_executor=executor,
        )
        assert len(executor.calls) == 1
        path, config, evts = executor.calls[0]
        assert path == "workflows/deploy.toml"
        assert config == {"env": "prod"}
        assert evts == events

    async def test_no_executor_raises(self) -> None:
        action = WorkflowAction(workflow_path="w.toml")
        with pytest.raises(RuntimeError, match="ActionExecutor"):
            await execute_action(action, [])


# -- EventAction tests --


class TestEventAction:
    async def test_fires_event(self) -> None:
        fired: list[tuple[str, dict[str, object] | None]] = []

        async def callback(
            event_type: str,
            data: dict[str, object] | None,
        ) -> None:
            fired.append((event_type, data))

        action = EventAction(
            event_type="task.started",
            data={"task": "build"},
            source="internal",
        )
        await execute_action(
            action,
            [],
            event_fire_callback=callback,
        )
        assert len(fired) == 1
        assert fired[0][0] == "task.started"
        assert fired[0][1] == {"task": "build"}

    async def test_empty_data_sends_none(self) -> None:
        fired: list[tuple[str, dict[str, object] | None]] = []

        async def callback(
            event_type: str,
            data: dict[str, object] | None,
        ) -> None:
            fired.append((event_type, data))

        action = EventAction(event_type="ping")
        await execute_action(action, [], event_fire_callback=callback)
        assert fired[0][1] is None

    async def test_no_callback_raises(self) -> None:
        action = EventAction(event_type="task.started")
        with pytest.raises(RuntimeError, match="event_fire_callback"):
            await execute_action(action, [])


# -- Unknown action type --


class TestUnknownAction:
    async def test_unknown_type_raises(self) -> None:
        with pytest.raises(TypeError, match="Unknown action type"):
            await execute_action("not_an_action", [])  # type: ignore[arg-type]


# -- Bounded concurrency --


class TestBoundedConcurrency:
    async def test_all_log_actions_complete(self) -> None:
        """All actions in the list are executed."""
        actions: list[tuple[Any, list[dict[str, object]]]] = [
            (LogAction(message=f"action-{i}"), [{"i": i}])
            for i in range(5)
        ]
        await execute_actions_bounded(actions, max_concurrent=2)

    async def test_empty_list_is_noop(self) -> None:
        await execute_actions_bounded([], max_concurrent=5)

    async def test_concurrent_script_actions(self) -> None:
        """ScriptActions execute concurrently up to the semaphore limit."""
        actions: list[tuple[Any, list[dict[str, object]]]] = [
            (
                ScriptAction(callable="_handlers:sample_sync_handler"),
                [{"batch": i}],
            )
            for i in range(4)
        ]
        await execute_actions_bounded(actions, max_concurrent=2)
        assert len(script_calls) == 4

    async def test_workflow_actions_with_executor(self) -> None:
        """WorkflowActions pass through to the executor under bounded concurrency."""
        executor = FakeWorkflowExecutor()
        actions: list[tuple[Any, list[dict[str, object]]]] = [
            (
                WorkflowAction(workflow_path=f"wf-{i}.toml"),
                [{"i": i}],
            )
            for i in range(3)
        ]
        await execute_actions_bounded(
            actions,
            max_concurrent=2,
            workflow_executor=executor,
        )
        assert len(executor.calls) == 3
