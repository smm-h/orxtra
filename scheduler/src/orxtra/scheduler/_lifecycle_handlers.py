from __future__ import annotations

import logging
from decimal import Decimal
from typing import Any
from uuid import UUID

from orxtra.protocols._task import TaskSpec, TaskState
from orxtra.protocols._tool import ToolError
from orxtra.protocols._tools import (
    CreateTaskParams,
    CreateWaitForParams,
    CreateWorkflowParams,
)
from orxtra.scheduler._base import SchedulerBase

_logger = logging.getLogger("orxtra.scheduler")


class LifecycleHandlersMixin(SchedulerBase):
    """Mixin for task lifecycle handlers (start, end, create, await)."""

    async def handle_start_task(
        self, session_id: str, task_id: str,
    ) -> str:
        task_uuid = UUID(task_id)
        if task_uuid not in self._task_states:
            msg = f"Task {task_id} does not exist"
            raise ToolError(msg)

        state = self._task_states[task_uuid]
        if state != TaskState.CREATED:
            msg = (
                f"Task {task_id} is in state '{state}',"
                " expected 'created'"
            )
            raise ToolError(msg)

        self._task_states[task_uuid] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_uuid, TaskState.PRECHECKING.value,
        )

        task = self._task_specs[task_uuid]
        precheck_results = await self._run_prechecks(
            task, task_uuid,
        )
        if not all(
            cr.passed for cr in precheck_results
        ):
            self._task_states[task_uuid] = (
                TaskState.PRECHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_uuid, TaskState.PRECHECK_FAILED.value,
            )
            failed = [
                cr.message
                for cr in precheck_results
                if not cr.passed
            ]
            return (
                f"Prechecks failed: {'; '.join(failed)}"
            )

        self._task_states[task_uuid] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_uuid, TaskState.ACTIVE.value,
        )
        self._active_tasks[session_id] = task_uuid
        return f"Task {task_id} is now active"

    async def handle_end_task(
        self,
        session_id: str,
        message: str,
    ) -> str:
        task_id = self.check_active_task(session_id)
        task = self._task_specs[task_id]

        children = self._task_children.get(task_id, [])
        incomplete = [
            cid
            for cid in children
            if self._task_states.get(cid)
            != TaskState.COMPLETED
        ]
        if incomplete:
            n = len(incomplete)
            msg = (
                f"Cannot end task:"
                f" {n} subtask(s) not complete"
            )
            raise ToolError(msg)

        self._task_states[task_id] = TaskState.POSTCHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.POSTCHECKING.value,
        )

        self._pending_end_task_message[task_id] = message
        await self._auto_commit(session_id, message)
        check_results = await self._run_postchecks(
            task, task_id,
        )
        all_passed = all(
            cr.passed for cr in check_results
        )

        if all_passed:
            # Run mechanical constraints
            constraint_results = (
                await self._run_mechanical_constraints(
                    task_id,
                )
            )
            constraint_passed = all(
                cr.passed for cr in constraint_results
            )
            if not constraint_passed:
                self._task_states[task_id] = (
                    TaskState.POSTCHECK_FAILED
                )
                await self._trace_writer.transition_task(
                    task_id,
                    TaskState.POSTCHECK_FAILED.value,
                )
                failed = [
                    cr.message
                    for cr in constraint_results
                    if not cr.passed
                ]
                return (
                    "Constraint check failed:"
                    f" {'; '.join(failed)}"
                )

            self._complete_task(
                task_id, task.name, message,
            )
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )
            del self._active_tasks[session_id]
            if task.on_success is not None:
                try:
                    parent_id = self._task_parents.get(
                        task_id,
                    )
                    await self._call_callback(
                        task.on_success,
                        self._make_task_context(
                            task, task_id, parent_id,
                            1, [], None,
                        ),
                    )
                except Exception:
                    _logger.exception("on_success callback failed")
            return f"Task {task_id} completed"

        self._task_states[task_id] = (
            TaskState.POSTCHECK_FAILED
        )
        await self._trace_writer.transition_task(
            task_id, TaskState.POSTCHECK_FAILED.value,
        )
        failed = [
            cr.message
            for cr in check_results
            if not cr.passed
        ]
        return (
            f"Postchecks failed: {'; '.join(failed)}"
        )

    async def handle_create_task(
        self,
        session_id: str,
        params: dict[str, Any] | CreateTaskParams,
    ) -> str:
        parent_id = self.check_active_task(session_id)
        if self._budget_blocked:
            msg = "Budget exhausted: new task creation blocked"
            raise ToolError(msg)
        parsed = (
            params
            if isinstance(params, CreateTaskParams)
            else CreateTaskParams(**params)
        )

        task_id = await self._trace_writer.create_task(
            run_id=self._run_id,
            parent_task_id=parent_id,
            name=parsed.name,
            task_type="agent",
        )
        self._task_states[task_id] = TaskState.CREATED

        # Resolve agent defaults for fields not
        # specified in the create_task call
        effective_write_paths = parsed.write_paths
        effective_budget = parsed.budget
        effective_timeout = parsed.timeout
        if parsed.agent:
            agent_def = self._agents.get(parsed.agent)
            if agent_def is not None:
                if effective_write_paths is None:
                    effective_write_paths = (
                        agent_def.write_paths
                    )
                if effective_budget is None:
                    effective_budget = agent_def.budget
                if effective_timeout is None:
                    effective_timeout = agent_def.timeout

        spec = TaskSpec(
            name=parsed.name,
            agent=parsed.agent,
            task_prompt=parsed.task_prompt,
            prechecks=list(parsed.prechecks),
            postchecks=list(parsed.postchecks),
            timeout=effective_timeout,
            context_refinement=parsed.context_refinement,
            budget=effective_budget,
            write_paths=effective_write_paths,
            category=parsed.category,
            retry=parsed.retry,
            retry_resume=parsed.retry_resume,
            retry_inject_failure=(
                parsed.retry_inject_failure
            ),
        )
        self._task_specs[task_id] = spec
        self._task_parents[task_id] = parent_id
        self._task_children.setdefault(
            parent_id, [],
        ).append(task_id)
        self._task_costs[task_id] = Decimal(0)

        # Claim write paths in file lock registry
        if effective_write_paths:
            try:
                self._file_lock_registry.claim(
                    task_id, effective_write_paths,
                )
            except ValueError as e:
                raise ToolError(str(e)) from e

        # Re-analyze parent's subtree for advisories
        children = self._task_children.get(
            parent_id, [],
        )
        advisories = self._analyze_structural_advisories(
            children,
        )
        if advisories:
            self._pending_advisories.extend(advisories)

        return str(task_id)

    async def handle_create_workflow(
        self,
        session_id: str,
        params: CreateWorkflowParams | dict[str, Any],
    ) -> str:
        parent_id = self.check_active_task(session_id)
        if self._budget_blocked:
            msg = "Budget exhausted: new workflow creation blocked"
            raise ToolError(msg)
        parsed = CreateWorkflowParams.model_validate(
            params if isinstance(params, dict) else params.model_dump()
        )

        config_data = {
            "description": parsed.description,
            "goals": parsed.goals,
        }
        workflow_id = await self._trace_writer.create_task(
            run_id=self._run_id,
            parent_task_id=parent_id,
            name=parsed.name,
            task_type="workflow",
            config=config_data,
        )
        spec = TaskSpec(
            name=parsed.name,
            subtasks=[],
            postchecks=list(parsed.postchecks),
            budget=parsed.budget,
        )
        self._task_states[workflow_id] = TaskState.CREATED
        self._task_specs[workflow_id] = spec
        self._task_parents[workflow_id] = parent_id
        self._task_children.setdefault(
            parent_id, [],
        ).append(workflow_id)
        self._task_children[workflow_id] = []
        self._task_costs[workflow_id] = Decimal(0)
        return str(workflow_id)

    async def handle_create_wait_for(
        self,
        session_id: str,
        params: dict[str, Any],
    ) -> str:
        parent_id = self.check_active_task(session_id)
        parsed = CreateWaitForParams(**params)

        task_id = await self._trace_writer.create_task(
            run_id=self._run_id,
            parent_task_id=parent_id,
            name=parsed.name,
            task_type="wait_for",
        )
        self._task_states[task_id] = TaskState.CREATED
        spec = TaskSpec(
            name=parsed.name,
            wait_for=parsed.event_name,
            timeout=parsed.timeout,
        )
        self._task_specs[task_id] = spec
        self._task_parents[task_id] = parent_id
        self._task_children.setdefault(
            parent_id, [],
        ).append(task_id)
        self._task_costs[task_id] = Decimal(0)
        self._event_registry.register(
            parsed.event_name, task_id,
        )
        return str(task_id)

    async def handle_await_task(
        self,
        session_id: str,
        task_id: str,
    ) -> str:
        task_uuid = UUID(task_id)
        if task_uuid not in self._task_states:
            msg = f"Task {task_id} does not exist"
            raise ToolError(msg)
        self._pending_await[session_id] = task_id
        return (
            f"Awaiting task {task_id}."
            " Session will suspend and resume"
            " with the result."
        )

    def check_active_task(self, session_id: str) -> UUID:
        task_id = self._active_tasks.get(session_id)
        if task_id is None:
            msg = (
                "No active task for session"
                f" '{session_id}'"
            )
            raise ToolError(msg)
        return task_id
