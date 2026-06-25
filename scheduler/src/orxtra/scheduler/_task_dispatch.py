from __future__ import annotations

import asyncio
import importlib
import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any

from orxtra.notepad import format_notepad
from orxtra.protocols import (
    CheckResult,
    TaskContext,
    TaskResult,
    TaskSpec,
    TaskState,
)

if TYPE_CHECKING:
    from uuid import UUID

from orxtra.scheduler._base import SchedulerBase
from orxtra.scheduler._graph import (
    build_graph,
    find_parallel_groups,
    topological_sort,
)

_logger = logging.getLogger("orxtra.scheduler")


class TaskDispatchMixin(SchedulerBase):
    """Mixin for composite, function, wait-for,
    decision-point, and for-each task execution."""

    async def _execute_decision_point_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult:
        """Execute a decision point task.

        Sends an event to the Overseer, which may create
        tasks, modify constraints, etc. in response.
        """
        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )
        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        await self._trace_writer.write_event(
            run_id=self._run_id,
            event_type="decision_point",
            data={
                "task_id": str(task_id),
                "task_name": task.name,
            },
            task_id=task_id,
        )

        # Send to Overseer (or headless fallback)
        from orxtra.protocols import (  # noqa: PLC0415
            StructuralAdvisory,
        )
        await self._send_overseer_event(
            StructuralAdvisory(
                task_id=task_id,
                observation=(
                    f"Decision point reached:"
                    f" {task.name}"
                ),
                suggestion=(
                    "Review context and decide how"
                    " to proceed. You may create"
                    " tasks, add constraints, or"
                    " take other actions."
                ),
            ),
        )

        self._complete_task(task_id, task.name, None)
        await self._trace_writer.transition_task(
            task_id, TaskState.COMPLETED.value,
        )
        return TaskResult(
            output=None,
            structured_output=None,
            check_results=[
                CheckResult(
                    passed=True,
                    message="Decision point completed",
                ),
            ],
        )

    async def _execute_function_task(
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        if task.callable is None:
            msg = "Function task requires callable"
            raise ValueError(msg)

        attempt_id = (
            await self._trace_writer.create_task_attempt(
                task_id, 1,
            )
        )
        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )
        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        parts = task.callable.split(":")
        if len(parts) != 2:  # noqa: PLR2004
            msg = (
                f"Invalid callable path: {task.callable!r}"
                " (expected 'module.path:function')"
            )
            raise ValueError(msg)
        module_path, func_name = parts
        module = importlib.import_module(module_path)
        func = getattr(module, func_name)

        # Compute nesting depth
        depth = 0
        current = task_id
        while self._task_parents.get(current) is not None:
            depth += 1
            current = self._task_parents[current]  # type: ignore[assignment]

        context = TaskContext(
            variables=variables or {},
            run_id=self._run_id,
            task_name=task.name,
            task_id=task_id,
            attempt=1,
            prior_attempts=None,
            notepad_content=format_notepad(
                self._notepad_entries,
            ),
            parent_task_id=parent_task_id,
            nesting_depth=depth,
        )

        result: TaskResult = await func(context)

        await self._trace_writer.complete_task_attempt(
            attempt_id=attempt_id,
            agent_output=result.output or "",
            structured_output=result.structured_output,
            check_result=None,
            check_verdict="pass",
            session_id=None,
            input_tokens=0,
            output_tokens=0,
            reasoning_tokens=0,
            cache_read_tokens=0,
            cache_write_tokens=0,
            cost_usd=Decimal(0),
            duration_seconds=0.0,
        )

        self._complete_task(
            task_id,
            task.name,
            result.output,
            structured=result.structured_output,
        )
        await self._trace_writer.transition_task(
            task_id, TaskState.COMPLETED.value,
        )
        return result

    async def _execute_composite_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult:
        if task.subtasks is None:
            msg = "Composite task requires subtasks"
            raise ValueError(msg)

        # Run prechecks before activating
        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )
        precheck_results = await self._run_prechecks(
            task, task_id,
        )
        if not all(
            cr.passed for cr in precheck_results
        ):
            self._task_states[task_id] = (
                TaskState.PRECHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id,
                TaskState.PRECHECK_FAILED.value,
            )
            return TaskResult(
                output=None,
                structured_output=None,
                check_results=precheck_results,
            )

        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        # Check if any subtask has dependencies
        has_deps = any(
            sub.depends_on for sub in task.subtasks
        )

        if has_deps:
            # Build dependency graph and execute in parallel groups
            deps: dict[str, list[str]] = {}
            for sub in task.subtasks:
                if sub.depends_on:
                    deps[sub.name] = list(sub.depends_on)
            graph = build_graph(task.subtasks, deps)
            order = topological_sort(graph)
            groups = find_parallel_groups(graph, order)

            name_to_sub = {
                sub.name: sub for sub in task.subtasks
            }
            for group in groups:
                group_tasks = [
                    asyncio.create_task(
                        self.execute_task(
                            name_to_sub[name], task_id,
                        ),
                    )
                    for name in group
                ]
                for t in group_tasks:
                    self._running_tasks.add(t)
                    t.add_done_callback(
                        self._running_tasks.discard,
                    )
                await asyncio.gather(*group_tasks)
        else:
            tasks = [
                asyncio.create_task(
                    self.execute_task(sub, task_id),
                )
                for sub in task.subtasks
            ]
            for t in tasks:
                self._running_tasks.add(t)
                t.add_done_callback(
                    self._running_tasks.discard,
                )
            await asyncio.gather(*tasks)

        # Run postchecks before completing
        self._task_states[task_id] = (
            TaskState.POSTCHECKING
        )
        await self._trace_writer.transition_task(
            task_id, TaskState.POSTCHECKING.value,
        )
        postcheck_results = await self._run_postchecks(
            task, task_id,
        )
        if not all(
            cr.passed for cr in postcheck_results
        ):
            self._task_states[task_id] = (
                TaskState.POSTCHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id,
                TaskState.POSTCHECK_FAILED.value,
            )
            return TaskResult(
                output=None,
                structured_output=None,
                check_results=postcheck_results,
            )

        self._task_states[task_id] = TaskState.COMPLETED
        await self._trace_writer.transition_task(
            task_id, TaskState.COMPLETED.value,
        )
        return TaskResult(
            output=None,
            structured_output=None,
            check_results=postcheck_results,
        )

    async def _execute_wait_for_task(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> TaskResult:
        if task.wait_for is None:
            msg = "Wait-for task requires wait_for"
            raise ValueError(msg)

        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )
        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        seconds = (
            float(task.timeout) if task.timeout else 60.0
        )
        payload = await self._event_registry.wait_for(
            task.wait_for, deadline_seconds=seconds,
        )

        if payload is not None:
            self._task_states[task_id] = TaskState.COMPLETED
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )
            return TaskResult(
                output=str(payload),
                structured_output=dict(payload),
                check_results=[
                    CheckResult(
                        passed=True,
                        message="Event received",
                    ),
                ],
            )

        self._task_states[task_id] = TaskState.CANCELLED
        await self._trace_writer.transition_task(
            task_id, TaskState.CANCELLED.value,
        )
        return TaskResult(
            output=None,
            structured_output=None,
            check_results=[
                CheckResult(
                    passed=False,
                    message="Wait timed out",
                ),
            ],
        )

    async def _execute_for_each(  # noqa: C901
        self,
        task: TaskSpec,
        task_id: UUID,
        parent_task_id: UUID | None,  # noqa: ARG002
        variables: dict[str, Any] | None = None,
    ) -> TaskResult:
        if (
            task.for_each is None
            or task.max_concurrency is None
        ):
            msg = (
                "for_each task requires for_each"
                " and max_concurrency"
            )
            raise ValueError(msg)

        items = (variables or {}).get(task.for_each, [])
        if not isinstance(items, list):
            msg = (
                f"for_each variable '{task.for_each}'"
                " must resolve to a list"
            )
            raise TypeError(msg)

        self._task_states[task_id] = TaskState.PRECHECKING
        await self._trace_writer.transition_task(
            task_id, TaskState.PRECHECKING.value,
        )
        self._task_states[task_id] = TaskState.ACTIVE
        await self._trace_writer.transition_task(
            task_id, TaskState.ACTIVE.value,
        )

        semaphore = asyncio.Semaphore(task.max_concurrency)
        results: list[TaskResult | None] = (
            [None] * len(items)
        )
        abort = False

        async def run_iteration(
            idx: int, item: object,
        ) -> None:
            nonlocal abort
            if abort:
                return
            async with semaphore:
                if abort:
                    return
                iteration_id = await self._trace_writer.create_iteration(
                    task_id, idx, item,
                )
                iter_vars = dict(variables or {})
                iter_vars["item"] = item

                iter_task = task.model_copy(
                    update={
                        "name": f"{task.name}_iter_{idx}",
                        "for_each": None,
                        "for_each_abort_on_failure": None,
                        "max_concurrency": None,
                    },
                )
                try:
                    result = await self.execute_task(
                        iter_task,
                        task_id,
                        variables=iter_vars,
                    )
                    results[idx] = result
                    check_dicts = (
                        [
                            {"passed": cr.passed, "message": cr.message}
                            for cr in result.check_results
                        ]
                        if result.check_results
                        else None
                    )
                    await self._trace_writer.complete_iteration(
                        iteration_id,
                        result.output,
                        result.structured_output,
                        check_dicts,
                    )
                    if (
                        not all(
                            cr.passed
                            for cr in result.check_results
                        )
                        and task.for_each_abort_on_failure
                    ):
                        abort = True
                except Exception as exc:  # noqa: BLE001
                    await self._trace_writer.fail_iteration(
                        iteration_id, str(exc),
                    )
                    if task.for_each_abort_on_failure:
                        abort = True

        async_tasks = [
            asyncio.create_task(
                run_iteration(i, item),
            )
            for i, item in enumerate(items)
        ]
        for t in async_tasks:
            self._running_tasks.add(t)
            t.add_done_callback(self._running_tasks.discard)
        await asyncio.gather(*async_tasks)

        if abort:
            self._task_states[task_id] = (
                TaskState.POSTCHECK_FAILED
            )
            await self._trace_writer.transition_task(
                task_id,
                TaskState.POSTCHECK_FAILED.value,
            )
        else:
            self._complete_task(
                task_id, task.name,
                json.dumps([
                    r.output if r is not None else None
                    for r in results
                ]),
            )
            await self._trace_writer.transition_task(
                task_id, TaskState.COMPLETED.value,
            )

        outputs = [
            r.output if r is not None else None
            for r in results
        ]
        return TaskResult(
            output=json.dumps(outputs),
            structured_output={"iterations": outputs},
            check_results=[
                CheckResult(
                    passed=not abort,
                    message="for_each complete",
                ),
            ],
        )
