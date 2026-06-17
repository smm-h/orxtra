"""Integration tests for Phase 2 scheduler features.

Tests verify integration, context assembly, task context,
mutation tracking, structured output validation, error
classification, decision points, file locks, structural
advisories, and mechanical constraints.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import types
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, patch

import pytest
import uuid6
from orxt.agent import Agent
from orxt.notepad import NotepadEntry, format_notepad
from orxt.protocols._errors import ErrorCategory
from orxt.protocols._execution import CheckResult, ScriptExecution
from orxt.protocols._task import TaskSpec, TaskState
from orxt.protocols._tool import ToolError
from orxt.protocols._tools import CreateTaskParams
from orxt.scheduler._executor import Scheduler, classify_error
from orxt.scheduler._types import WorkflowConfig
from orxt.transport import Result, StepFinish, ToolUse

from tests.conftest import (
    MockTraceWriter,
    MockTransport,
    make_agent,
    make_categories,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from orxt.transport import Event


# -- helpers --

def _simple_task(
    name: str = "t1",
    agent: str = "test-agent",
    timeout: int = 60,
    **kwargs: Any,  # noqa: ANN401
) -> TaskSpec:
    return TaskSpec(
        name=name,
        agent=agent,
        task_prompt=f"Do {name}",
        timeout=timeout,
        context_refinement=False,
        **kwargs,
    )


def _make_scheduler(
    trace_writer: MockTraceWriter,
    transport: Any,  # noqa: ANN401
    run_id: uuid.UUID,
    read_root: Path,
    agents: dict[str, Agent] | None = None,
    categories: dict[str, str] | None = None,
) -> Scheduler:
    return Scheduler(
        trace_writer=trace_writer,  # type: ignore[arg-type]
        transport_registry={"anthropic": transport},  # type: ignore[dict-item]
        agents=agents or {"test-agent": make_agent()},
        categories=categories or make_categories(),
        run_id=run_id,
        read_root=read_root,
    )


def _register_check_module(
    module_name: str,
    **functions: Any,  # noqa: ANN401
) -> types.ModuleType:
    """Register a synthetic module with check functions in sys.modules."""
    mod = types.ModuleType(module_name)
    for fname, func in functions.items():
        setattr(mod, fname, func)
    sys.modules[module_name] = mod
    # Ensure parent packages exist
    parts = module_name.split(".")
    for i in range(1, len(parts)):
        parent = ".".join(parts[:i])
        if parent not in sys.modules:
            sys.modules[parent] = types.ModuleType(parent)
    return mod


# -- Section 1: Verify Integration --

class TestVerifyIntegration:
    async def test_prechecks_call_verify_when_defined(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """ScriptExecution prechecks call verify.run_checks."""
        trace_writer = MockTraceWriter()
        check_called = False

        async def passing_check(ctx: Any) -> CheckResult:  # noqa: ANN401
            nonlocal check_called
            check_called = True
            return CheckResult(
                passed=True, message="All good",
            )

        _register_check_module(
            "tests.int_check_pre",
            passing_check=passing_check,
        )

        try:
            task = TaskSpec(
                name="pre-verify",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                prechecks=[
                    ScriptExecution(
                        callable="tests.int_check_pre:passing_check",
                    ),
                ],
            )
            sched = _make_scheduler(
                trace_writer, MockTransport(), run_id,
                read_root=tmp_path,
            )
            config = WorkflowConfig(
                name="pre-wf",
                description="Precheck test",
                tasks=[task],
                dependencies={},
            )
            await sched.execute_workflow(config)
            assert check_called
        finally:
            sys.modules.pop("tests.int_check_pre", None)

    async def test_postchecks_call_verify_when_defined(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """ScriptExecution postchecks call verify.run_checks."""
        trace_writer = MockTraceWriter()
        postcheck_called = False

        async def post_check(ctx: Any) -> CheckResult:  # noqa: ANN401
            nonlocal postcheck_called
            postcheck_called = True
            return CheckResult(
                passed=True, message="Post OK",
            )

        _register_check_module(
            "tests.int_check_post",
            post_check=post_check,
        )

        try:
            task = TaskSpec(
                name="post-verify",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                postchecks=[
                    ScriptExecution(
                        callable="tests.int_check_post:post_check",
                    ),
                ],
            )
            sched = _make_scheduler(
                trace_writer, MockTransport(), run_id,
                read_root=tmp_path,
            )
            config = WorkflowConfig(
                name="post-wf",
                description="Postcheck test",
                tasks=[task],
                dependencies={},
            )
            await sched.execute_workflow(config)
            assert postcheck_called
        finally:
            sys.modules.pop("tests.int_check_post", None)

    async def test_no_checks_returns_stub_passed(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """When no checks defined, returns stub passed result."""
        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001

        pre_results = await scheduler._run_prechecks(  # noqa: SLF001
            task, task_id,
        )
        assert len(pre_results) == 1
        assert pre_results[0].passed is True
        assert "No prechecks" in pre_results[0].message

        post_results = await scheduler._run_postchecks(  # noqa: SLF001
            task, task_id,
        )
        assert len(post_results) == 1
        assert post_results[0].passed is True
        assert "No postchecks" in post_results[0].message


# -- Section 2: Context Assembly --

class TestContextAssembly:
    async def test_constraints_injected_into_prompt(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """Active constraints are injected as '## Active Constraints'."""
        trace_writer = MockTraceWriter()
        received_prompts: list[str] = []

        class CapturingTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                received_prompts.append(message)
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        sched = _make_scheduler(
            trace_writer, CapturingTransport(), run_id,
            read_root=tmp_path,
        )
        sched._active_constraints.append(  # noqa: SLF001
            ("No new dependencies", "mechanical"),
        )
        config = WorkflowConfig(
            name="constraint-wf",
            description="Constraint test",
            tasks=[_simple_task()],
            dependencies={},
        )
        await sched.execute_workflow(config)

        assert len(received_prompts) >= 1
        prompt = received_prompts[0]
        assert "## Active Constraints" in prompt
        assert "No new dependencies" in prompt
        assert "mechanical" in prompt

    async def test_notepad_entries_injected_into_prompt(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """Notepad entries formatted and injected into prompt."""
        trace_writer = MockTraceWriter()
        received_prompts: list[str] = []

        class CapturingTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                received_prompts.append(message)
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        sched = _make_scheduler(
            trace_writer, CapturingTransport(), run_id,
            read_root=tmp_path,
        )
        entry = NotepadEntry(
            run_id=run_id,
            task_name="prior-task",
            agent_name="analyst",
            entry_type="learning",
            text="The API uses v2 endpoints",
            created_at=datetime.now(UTC),
        )
        sched._notepad_entries.append(entry)  # noqa: SLF001

        config = WorkflowConfig(
            name="notepad-wf",
            description="Notepad test",
            tasks=[_simple_task()],
            dependencies={},
        )
        await sched.execute_workflow(config)

        assert len(received_prompts) >= 1
        prompt = received_prompts[0]
        assert "Context from previous steps" in prompt
        assert "The API uses v2 endpoints" in prompt

    async def test_prior_failure_context_injected(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """Prior failure context injected on retry with retry_inject_failure."""
        trace_writer = MockTraceWriter()
        received_prompts: list[str] = []
        call_count = 0

        async def fail_then_pass(
            self: Scheduler,
            task: TaskSpec,
            task_id: uuid.UUID,
        ) -> list[CheckResult]:
            nonlocal call_count
            call_count += 1
            if call_count <= 1:
                return [
                    CheckResult(
                        passed=False,
                        message="First fail",
                    ),
                ]
            return [
                CheckResult(
                    passed=True, message="OK",
                ),
            ]

        class CapturingTransport:
            async def send(
                self, message: str, **kwargs: Any,  # noqa: ANN401
            ) -> AsyncIterator[Event]:
                received_prompts.append(message)
                tools = kwargs.get("tools", [])
                tool_map = {t.name: t for t in tools}
                task_id_match = re.search(
                    r"Your task ID is ([0-9a-f-]+)",
                    message,
                )
                task_id_str = (
                    task_id_match.group(1)
                    if task_id_match
                    else "unknown"
                )
                if "start_task" in tool_map:
                    r = await tool_map[
                        "start_task"
                    ].execute({"task_id": task_id_str})
                    yield ToolUse(
                        tool_name="start_task",
                        input={"task_id": task_id_str},
                        output=r,
                        status="success",
                    )
                if "end_task" in tool_map:
                    r = await tool_map[
                        "end_task"
                    ].execute({"message": "done"})
                    yield ToolUse(
                        tool_name="end_task",
                        input={"message": "done"},
                        output=r,
                        status="success",
                    )
                sid = kwargs.get("session_id") or str(
                    uuid6.uuid7(),
                )
                yield StepFinish(
                    reason="end_turn",
                    input_tokens=10,
                    output_tokens=5,
                    reasoning_tokens=0,
                    cache_read_tokens=0,
                    cache_write_tokens=0,
                )
                yield Result(
                    text="done",
                    session_id=sid,
                    total_input_tokens=10,
                    total_output_tokens=5,
                    total_reasoning_tokens=0,
                    total_cache_read_tokens=0,
                    total_cache_write_tokens=0,
                    tool_calls=2,
                )

        original = Scheduler._run_postchecks  # noqa: SLF001
        Scheduler._run_postchecks = fail_then_pass  # type: ignore[assignment]  # noqa: SLF001
        try:
            task = TaskSpec(
                name="retry-ctx",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                retry=2,
                retry_resume=False,
                retry_inject_failure=True,
            )
            config = WorkflowConfig(
                name="retry-ctx-wf",
                description="Retry context test",
                tasks=[task],
                dependencies={},
            )
            sched = _make_scheduler(
                trace_writer, CapturingTransport(), run_id,
                read_root=tmp_path,
            )
            await sched.execute_workflow(config)

            assert len(received_prompts) >= 2
            retry_prompt = received_prompts[1]
            assert "## Prior Failure Context" in retry_prompt
            assert "Prior attempt" in retry_prompt
        finally:
            Scheduler._run_postchecks = original  # type: ignore[assignment]  # noqa: SLF001


# -- Section 3: TaskContext --

class TestTaskContext:
    async def test_nesting_depth_computed(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Nesting depth walks _task_parents chain."""
        # Create grandparent -> parent -> child
        gp_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="gp",
            task_type="agent",
        )
        p_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=gp_id,
            name="p",
            task_type="agent",
        )
        c_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=p_id,
            name="c",
            task_type="agent",
        )

        gp_task = _simple_task("gp")
        p_task = _simple_task("p")
        c_task = _simple_task("c")

        scheduler._init_task_state(gp_id, gp_task, None)  # noqa: SLF001
        scheduler._init_task_state(p_id, p_task, gp_id)  # noqa: SLF001
        scheduler._init_task_state(c_id, c_task, p_id)  # noqa: SLF001

        ctx = scheduler._make_task_context(  # noqa: SLF001
            c_task, c_id, p_id, 1, [], None,
        )
        assert ctx.nesting_depth == 2

        ctx_p = scheduler._make_task_context(  # noqa: SLF001
            p_task, p_id, gp_id, 1, [], None,
        )
        assert ctx_p.nesting_depth == 1

        ctx_gp = scheduler._make_task_context(  # noqa: SLF001
            gp_task, gp_id, None, 1, [], None,
        )
        assert ctx_gp.nesting_depth == 0

    async def test_notepad_content_populated(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """TaskContext notepad_content uses format_notepad."""
        entry = NotepadEntry(
            run_id=run_id,
            task_name="prior",
            agent_name="agent-x",
            entry_type="decision",
            text="Use approach B",
            created_at=datetime.now(UTC),
        )
        scheduler._notepad_entries.append(entry)  # noqa: SLF001

        task = _simple_task()
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="t1",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001

        ctx = scheduler._make_task_context(  # noqa: SLF001
            task, task_id, None, 1, [], None,
        )
        expected = format_notepad([entry])
        assert ctx.notepad_content == expected
        assert "Use approach B" in ctx.notepad_content


# -- Section 4: Mutation Tracking --

class TestMutationTracking:
    async def test_session_mutations_tracked(
        self,
        scheduler: Scheduler,
    ) -> None:
        """_session_mutations dict tracks mutation state."""
        scheduler._session_mutations["sess-1"] = False  # noqa: SLF001
        assert scheduler._session_mutations["sess-1"] is False  # noqa: SLF001
        scheduler._session_mutations["sess-1"] = True  # noqa: SLF001
        assert scheduler._session_mutations["sess-1"] is True  # noqa: SLF001

    async def test_auto_commit_runs_on_mutations(
        self,
        scheduler: Scheduler,
    ) -> None:
        """_auto_commit runs git status and safegit when mutations detected."""
        scheduler._session_mutations["sess-a"] = True  # noqa: SLF001

        # Mock subprocess to simulate dirty working tree
        mock_git_status = AsyncMock()
        mock_git_status.communicate = AsyncMock(
            return_value=(b" M file.py\n", b""),
        )
        mock_git_status.returncode = 0

        mock_safegit = AsyncMock()
        mock_safegit.communicate = AsyncMock(
            return_value=(b"", b""),
        )
        mock_safegit.returncode = 0

        call_count = 0

        async def mock_subprocess(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return mock_git_status
            return mock_safegit

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=mock_subprocess,
        ):
            await scheduler._auto_commit(  # noqa: SLF001
                "sess-a", "test commit",
            )

        # git status was called, then safegit commit
        assert call_count == 2

    async def test_auto_commit_warns_on_tracker_disagree_clean(
        self,
        scheduler: Scheduler,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning when mutation tracker says yes but git is clean."""
        scheduler._session_mutations["sess-b"] = True  # noqa: SLF001

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b"", b""),  # clean
        )

        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            caplog.at_level(logging.WARNING, logger="orxt.scheduler"),
        ):
            await scheduler._auto_commit(  # noqa: SLF001
                "sess-b", "msg",
            )

        assert any(
            "Mutation tracker detected changes" in r.message
            for r in caplog.records
        )

    async def test_auto_commit_warns_on_tracker_disagree_dirty(
        self,
        scheduler: Scheduler,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """Warning when git has changes but tracker reports none."""
        scheduler._session_mutations["sess-c"] = False  # noqa: SLF001

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(
            return_value=(b" M dirty.py\n", b""),
        )

        with (
            patch(
                "asyncio.create_subprocess_exec",
                return_value=mock_proc,
            ),
            caplog.at_level(logging.WARNING, logger="orxt.scheduler"),
        ):
            await scheduler._auto_commit(  # noqa: SLF001
                "sess-c", "msg",
            )

        assert any(
            "mutation tracker reports none" in r.message
            for r in caplog.records
        )


# -- Section 5: Structured Output Validation --

class TestStructuredOutputValidation:
    async def test_valid_output_passes(
        self,
        scheduler: Scheduler,
    ) -> None:
        """Valid JSON against schema returns passed."""
        schema = json.dumps({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        })
        result = scheduler._validate_output_schema(  # noqa: SLF001
            '{"name": "test"}', schema,
        )
        assert result.passed is True
        assert "validated" in result.message.lower()

    async def test_invalid_output_fails(
        self,
        scheduler: Scheduler,
    ) -> None:
        """Invalid JSON against schema returns failed."""
        schema = json.dumps({
            "type": "object",
            "properties": {
                "name": {"type": "string"},
            },
            "required": ["name"],
        })
        result = scheduler._validate_output_schema(  # noqa: SLF001
            '{"count": 42}', schema,
        )
        assert result.passed is False
        assert "validation failed" in result.message.lower()

    async def test_none_output_fails(
        self,
        scheduler: Scheduler,
    ) -> None:
        """None output returns failed."""
        schema = json.dumps({"type": "object"})
        result = scheduler._validate_output_schema(  # noqa: SLF001
            None, schema,
        )
        assert result.passed is False
        assert "No output" in result.message

    async def test_invalid_json_output_fails(
        self,
        scheduler: Scheduler,
    ) -> None:
        """Non-JSON output returns failed."""
        schema = json.dumps({"type": "object"})
        result = scheduler._validate_output_schema(  # noqa: SLF001
            "not json at all", schema,
        )
        assert result.passed is False
        assert "not valid JSON" in result.message


# -- Section 6: Error Classification --

class TestErrorClassification:
    async def test_timeout_is_infra(self) -> None:
        assert classify_error(TimeoutError()) == ErrorCategory.INFRA

    async def test_json_decode_is_parse(self) -> None:
        err = json.JSONDecodeError("bad", "", 0)
        assert classify_error(err) == ErrorCategory.PARSE

    async def test_assertion_is_logic(self) -> None:
        assert classify_error(AssertionError()) == ErrorCategory.LOGIC

    async def test_import_is_build_env(self) -> None:
        assert classify_error(ImportError()) == ErrorCategory.BUILD_ENV

    async def test_runtime_is_unclassified(self) -> None:
        assert classify_error(RuntimeError()) == ErrorCategory.UNCLASSIFIED


# -- Section 7: Decision Point --

class TestDecisionPoint:
    async def test_decision_point_task_completes(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """Decision point tasks transition to ACTIVE, emit
        event, and complete."""
        trace_writer = MockTraceWriter()
        sched = _make_scheduler(
            trace_writer, MockTransport(), run_id,
            read_root=tmp_path,
        )

        task = TaskSpec(
            name="decide",
            decision_point=True,
        )
        result = await sched.execute_task(task, None)

        assert result.check_results[0].passed is True
        assert "Decision point" in result.check_results[0].message

        # Verify event was written
        events = trace_writer.get_calls("write_event")
        dp_events = [
            e for e in events
            if e["event_type"] == "decision_point"
        ]
        assert len(dp_events) == 1
        assert dp_events[0]["data"]["task_name"] == "decide"

        # Verify state is completed
        completed = [
            tid
            for tid, s in sched._task_states.items()  # noqa: SLF001
            if s == TaskState.COMPLETED
        ]
        assert len(completed) == 1


# -- Section 8: File Locks --

class TestFileLocks:
    async def test_file_lock_claimed_on_create_task(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """write_paths are claimed in file lock registry."""
        # Set up a parent task
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._init_task_state(  # noqa: SLF001
            parent_id, parent_task, None,
        )
        scheduler._task_states[parent_id] = TaskState.ACTIVE  # noqa: SLF001
        scheduler._active_tasks["sess-lock"] = parent_id  # noqa: SLF001

        params = CreateTaskParams(
            name="writer-task",
            agent="test-agent",
            task_prompt="Write files",
            timeout=60,
            context_refinement=False,
            write_paths=["src/main.py", "src/utils.py"],
        )
        task_id_str = await scheduler.handle_create_task(
            "sess-lock", params,
        )
        task_id = uuid.UUID(task_id_str)

        # Verify paths are claimed
        conflict = scheduler._file_lock_registry.check_conflict(  # noqa: SLF001
            ["src/main.py"],
        )
        assert conflict == task_id

    async def test_file_lock_conflict_raises(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Conflicting write_paths raise ToolError."""
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._init_task_state(  # noqa: SLF001
            parent_id, parent_task, None,
        )
        scheduler._task_states[parent_id] = TaskState.ACTIVE  # noqa: SLF001
        scheduler._active_tasks["sess-lock2"] = parent_id  # noqa: SLF001

        params1 = CreateTaskParams(
            name="writer-1",
            agent="test-agent",
            task_prompt="Write",
            timeout=60,
            context_refinement=False,
            write_paths=["src/shared.py"],
        )
        await scheduler.handle_create_task(
            "sess-lock2", params1,
        )

        params2 = CreateTaskParams(
            name="writer-2",
            agent="test-agent",
            task_prompt="Also write",
            timeout=60,
            context_refinement=False,
            write_paths=["src/shared.py"],
        )
        with pytest.raises(ToolError, match="conflict"):
            await scheduler.handle_create_task(
                "sess-lock2", params2,
            )

    async def test_file_lock_released_on_complete(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """File locks released when task completes."""
        parent_task = _simple_task("parent")
        parent_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="parent",
            task_type="agent",
        )
        scheduler._init_task_state(  # noqa: SLF001
            parent_id, parent_task, None,
        )
        scheduler._task_states[parent_id] = TaskState.ACTIVE  # noqa: SLF001
        scheduler._active_tasks["sess-lock3"] = parent_id  # noqa: SLF001

        params = CreateTaskParams(
            name="writer-rel",
            agent="test-agent",
            task_prompt="Write",
            timeout=60,
            context_refinement=False,
            write_paths=["src/release.py"],
        )
        task_id_str = await scheduler.handle_create_task(
            "sess-lock3", params,
        )
        task_id = uuid.UUID(task_id_str)

        # Complete the task -- locks should release
        scheduler._complete_task(  # noqa: SLF001
            task_id, "writer-rel", "done",
        )

        # Now the path should be free
        conflict = scheduler._file_lock_registry.check_conflict(  # noqa: SLF001
            ["src/release.py"],
        )
        assert conflict is None

    async def test_file_lock_released_on_abort(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """File locks released when abort is called."""
        task = _simple_task("locked-task")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="locked-task",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001
        scheduler._task_states[task_id] = TaskState.ACTIVE  # noqa: SLF001
        scheduler._file_lock_registry.claim(  # noqa: SLF001
            task_id, ["src/abort.py"],
        )

        await scheduler.abort()

        conflict = scheduler._file_lock_registry.check_conflict(  # noqa: SLF001
            ["src/abort.py"],
        )
        assert conflict is None


# -- Section 9: Structural Advisories --

class TestStructuralAdvisories:
    async def test_read_only_agents_detected(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Read-only agent tasks produce front_load advisory."""
        # Default make_agent has allow=["read"]
        task = _simple_task("reader")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="reader",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001

        advisories = scheduler._analyze_structural_advisories(  # noqa: SLF001
            [task_id],
        )
        assert len(advisories) == 1
        assert advisories[0]["type"] == "front_load_read_only"
        assert "reader" in advisories[0]["task_names"]

    async def test_write_capable_agents_no_advisory(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """Write-capable agents do not produce advisories."""
        trace_writer = MockTraceWriter()
        write_agent = Agent(
            name="writer-agent",
            description="Agent with write",
            prompt="You write.",
            category="default",
            allow=["read", "write", "edit"],
        )
        sched = _make_scheduler(
            trace_writer, MockTransport(), run_id,
            read_root=tmp_path,
            agents={"writer-agent": write_agent},
        )
        task = _simple_task("writer", agent="writer-agent")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="writer",
            task_type="agent",
        )
        sched._init_task_state(task_id, task, None)  # noqa: SLF001

        advisories = sched._analyze_structural_advisories(  # noqa: SLF001
            [task_id],
        )
        assert len(advisories) == 0


# -- Section 10: Mechanical Constraints --

class TestMechanicalConstraints:
    async def test_cheap_constraints_run_always(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Cheap constraints run even without subtasks."""
        task = _simple_task("leaf")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="leaf",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001
        scheduler._mechanical_constraints.append(  # noqa: SLF001
            ("No removed exports", "no_removed_exports"),
        )

        results = await scheduler._run_mechanical_constraints(  # noqa: SLF001
            task_id,
        )
        assert len(results) == 1
        assert results[0].passed is True
        assert "no_removed_exports" in results[0].message

    async def test_expensive_constraints_skipped_without_subtasks(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Expensive constraints (tests_pass, lint_clean) skip
        when task has no subtasks."""
        task = _simple_task("leaf")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="leaf",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001
        scheduler._mechanical_constraints.extend([  # noqa: SLF001
            ("Tests must pass", "tests_pass"),
            ("Lint clean", "lint_clean"),
            ("No new deps", "no_new_dependencies"),
        ])

        results = await scheduler._run_mechanical_constraints(  # noqa: SLF001
            task_id,
        )
        # Only the cheap one (no_new_dependencies) should run
        assert len(results) == 1
        assert "no_new_dependencies" in results[0].message

    async def test_expensive_constraints_run_with_subtasks(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Expensive constraints run when task has subtasks
        (workflow completion)."""
        task = _simple_task("workflow-task")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="workflow-task",
            task_type="agent",
        )
        scheduler._init_task_state(  # noqa: SLF001
            task_id, task, None,
        )

        # Give it a child
        child_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=task_id,
            name="child",
            task_type="agent",
        )
        child_task = _simple_task("child")
        scheduler._init_task_state(  # noqa: SLF001
            child_id, child_task, task_id,
        )
        scheduler._task_children[task_id].append(child_id)  # noqa: SLF001

        scheduler._mechanical_constraints.extend([  # noqa: SLF001
            ("Tests must pass", "tests_pass"),
            ("No new deps", "no_new_dependencies"),
        ])

        results = await scheduler._run_mechanical_constraints(  # noqa: SLF001
            task_id,
        )
        # Both should run because task has subtasks
        assert len(results) == 2
        messages = [r.message for r in results]
        assert any("tests_pass" in m for m in messages)
        assert any("no_new_dependencies" in m for m in messages)

    async def test_unknown_constraint_kind_fails(
        self,
        scheduler: Scheduler,
        trace_writer: MockTraceWriter,
        run_id: uuid.UUID,
    ) -> None:
        """Unknown constraint kind produces a failed result."""
        task = _simple_task("leaf")
        task_id = await trace_writer.create_task(
            run_id=run_id,
            parent_task_id=None,
            name="leaf",
            task_type="agent",
        )
        scheduler._init_task_state(task_id, task, None)  # noqa: SLF001
        scheduler._mechanical_constraints.append(  # noqa: SLF001
            ("Unknown check", "totally_bogus"),
        )

        results = await scheduler._run_mechanical_constraints(  # noqa: SLF001
            task_id,
        )
        assert len(results) == 1
        assert results[0].passed is False
        assert "Unknown constraint kind" in results[0].message


# -- Section: Fix-then-re-verify integration --

class TestFixThenReverify:
    async def test_fix_callback_retries_check(
        self,
        run_id: uuid.UUID,
        tmp_path: Path,
    ) -> None:
        """When a precheck fails with a fix callable, verify
        calls fix then re-runs the check."""
        trace_writer = MockTraceWriter()
        fix_called = False
        check_call_count = 0

        async def fixable_check(ctx: Any) -> CheckResult:  # noqa: ANN401
            nonlocal check_call_count
            check_call_count += 1
            if check_call_count == 1:
                async def do_fix(c: Any) -> None:  # noqa: ANN401
                    nonlocal fix_called
                    fix_called = True

                return CheckResult(
                    passed=False,
                    message="Needs fix",
                    fix=do_fix,
                )
            return CheckResult(
                passed=True,
                message="Fixed",
            )

        _register_check_module(
            "tests.int_fixable",
            fixable_check=fixable_check,
        )

        try:
            task = TaskSpec(
                name="fixable-task",
                agent="test-agent",
                task_prompt="Do work",
                timeout=60,
                context_refinement=False,
                prechecks=[
                    ScriptExecution(
                        callable="tests.int_fixable:fixable_check",
                    ),
                ],
            )
            sched = _make_scheduler(
                trace_writer, MockTransport(), run_id,
                read_root=tmp_path,
            )
            config = WorkflowConfig(
                name="fix-wf",
                description="Fix test",
                tasks=[task],
                dependencies={},
            )
            await sched.execute_workflow(config)

            assert fix_called
            assert check_call_count == 2
        finally:
            sys.modules.pop("tests.int_fixable", None)
