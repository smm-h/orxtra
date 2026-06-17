from __future__ import annotations

import json
import logging
from decimal import Decimal
from typing import TYPE_CHECKING

import jsonschema
from orxt.protocols._checks import CheckContext
from orxt.protocols._constraints import (
    EXPENSIVE_CONSTRAINTS,
    ConstraintKind,
)
from orxt.protocols._execution import CheckResult
from orxt.session import compute_cost_usd
from orxt.verify import run_checks

if TYPE_CHECKING:
    from typing import Any
    from uuid import UUID

    from orxt.protocols._task import TaskSpec
    from orxt.transport import Usage

_logger = logging.getLogger("orxt.scheduler")


class EnforcementMixin:
    """Mixin for budget enforcement, constraints, checks, and validation."""

    def _resolve_model_key(
        self, task: TaskSpec,
    ) -> str | None:
        if task.agent is None:
            return None
        agent_def = self._agents.get(task.agent)
        if agent_def is None:
            return None
        category_str = task.category or agent_def.category
        return self._categories.get(category_str)

    def _accumulate_cost(
        self,
        task_id: UUID,
        task: TaskSpec,
        usage: Usage,
    ) -> None:
        model_key = self._resolve_model_key(task)
        if model_key is None:
            return
        cost = compute_cost_usd(model_key, usage)
        self._task_costs[task_id] += cost
        # Budget enforcement
        if task.budget is not None:
            spent = self._task_costs[task_id]
            budget = task.budget
            threshold = Decimal("0.8")
            if (
                spent >= budget * threshold
                and spent - cost
                < budget * threshold
            ):
                self._budget_threshold_events.append(
                    (
                        task_id,
                        task.name,
                        budget,
                        spent,
                    ),
                )
            if spent >= budget:
                self._budget_exhausted_events.append(
                    (
                        task_id,
                        task.name,
                        budget,
                        spent,
                    ),
                )

    async def _send_budget_events(
        self, task_id: UUID,
    ) -> None:
        """Send accumulated budget events
        to the Overseer."""
        for tid, _name, budget, spent in (
            self._budget_threshold_events
        ):
            if tid == task_id:
                from orxt.protocols._events import (  # noqa: PLC0415
                    BudgetThresholdCrossed,
                )

                await self._send_overseer_event(
                    BudgetThresholdCrossed(
                        workflow_id=tid,
                        budget_usd=budget,
                        spent_usd=spent,
                        threshold_pct=0.8,
                    ),
                )
        self._budget_threshold_events = [
            e
            for e in self._budget_threshold_events
            if e[0] != task_id
        ]

        for tid, _name, _budget, _spent in (
            self._budget_exhausted_events
        ):
            if tid == task_id:
                from orxt.protocols._events import (  # noqa: PLC0415
                    BudgetExhausted,
                )

                await self._send_overseer_event(
                    BudgetExhausted(workflow_id=tid),
                )
        self._budget_exhausted_events = [
            e
            for e in self._budget_exhausted_events
            if e[0] != task_id
        ]

    async def _run_postchecks(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> list[CheckResult]:
        if not task.postchecks:
            return [
                CheckResult(
                    passed=True,
                    message="No postchecks defined",
                ),
            ]
        message = self._pending_end_task_message.get(
            task_id, "",
        )
        ctx = CheckContext(
            variables={},
            agent_output=message,
            run_id=self._run_id,
            session_id=None,
            task_name=task.name,
            task_id=task_id,
            attempt=1,
            parent_task_id=self._task_parents.get(
                task_id,
            ),
        )
        return await run_checks(
            task.postchecks, ctx, "postcheck", self,
        )

    async def _run_prechecks(
        self,
        task: TaskSpec,
        task_id: UUID,
    ) -> list[CheckResult]:
        if not task.prechecks:
            return [
                CheckResult(
                    passed=True,
                    message="No prechecks defined",
                ),
            ]
        ctx = CheckContext(
            variables={},
            agent_output=None,
            run_id=self._run_id,
            session_id=None,
            task_name=task.name,
            task_id=task_id,
            attempt=1,
            parent_task_id=self._task_parents.get(
                task_id,
            ),
        )
        return await run_checks(
            task.prechecks, ctx, "precheck", self,
        )

    async def _run_mechanical_constraints(
        self,
        task_id: UUID,
    ) -> list[CheckResult]:
        """Run active mechanical constraints.

        Cheap constraints run after every task.
        Expensive constraints (tests_pass, lint_clean)
        run only when the completing task has subtasks
        (workflow completion).
        """
        results: list[CheckResult] = []
        has_subtasks = bool(
            self._task_children.get(task_id),
        )

        for _text, kind_str in self._mechanical_constraints:
            try:
                kind = ConstraintKind(kind_str)
            except ValueError:
                results.append(CheckResult(
                    passed=False,
                    message=(
                        "Unknown constraint kind:"
                        f" {kind_str}"
                    ),
                ))
                continue

            # Skip expensive constraints unless this
            # is workflow completion
            if (
                kind in EXPENSIVE_CONSTRAINTS
                and not has_subtasks
            ):
                continue

            result = await self._check_constraint(
                kind, task_id,
            )
            results.append(result)

        return results

    async def _check_constraint(
        self,
        kind: ConstraintKind,
        task_id: UUID,  # noqa: ARG002
    ) -> CheckResult:
        """Check a single mechanical constraint.

        Stubs for now -- each ConstraintKind will have
        its own checker once implemented.
        """
        return CheckResult(
            passed=True,
            message=(
                f"Constraint {kind.value} passed (stub)"
            ),
        )

    def _validate_output_schema(
        self,
        output: str | None,
        schema_str: str,
    ) -> CheckResult:
        """Validate agent output against a JSON schema."""
        if output is None:
            return CheckResult(
                passed=False,
                message=(
                    "No output to validate against schema"
                ),
            )

        try:
            parsed = json.loads(output)
        except json.JSONDecodeError as e:
            return CheckResult(
                passed=False,
                message=f"Output is not valid JSON: {e}",
            )

        try:
            schema = json.loads(schema_str)
        except json.JSONDecodeError as e:
            return CheckResult(
                passed=False,
                message=(
                    "Output schema is not valid JSON:"
                    f" {e}"
                ),
            )

        try:
            jsonschema.validate(parsed, schema)
        except jsonschema.ValidationError as e:
            return CheckResult(
                passed=False,
                message=(
                    "Output validation failed:"
                    f" {e.message}"
                ),
            )

        return CheckResult(
            passed=True, message="Output validated",
        )

    def _analyze_structural_advisories(
        self,
        task_ids: list[UUID],
    ) -> list[dict[str, Any]]:
        """Analyze a set of tasks for structural improvements.

        Detects read-only tasks that could be front-loaded
        for earlier execution. Advisories are stored for the
        Overseer to consume in Phase 3.
        """
        advisories: list[dict[str, Any]] = []

        if not task_ids:
            return advisories

        # Detect read-only agent tasks
        write_tools = {
            "write", "edit", "delete", "move",
            "copy", "mkdir", "set_executable",
        }
        read_only_names: list[str] = []
        for task_id in task_ids:
            spec = self._task_specs.get(task_id)
            if spec is None or spec.agent is None:
                continue
            agent_def = self._agents.get(spec.agent)
            if agent_def is None:
                continue
            has_write = bool(
                set(agent_def.allow) & write_tools,
            )
            if not has_write:
                read_only_names.append(spec.name)

        if read_only_names:
            advisories.append({
                "type": "front_load_read_only",
                "message": (
                    f"Tasks {read_only_names} are read-only"
                    " and could be front-loaded for earlier"
                    " execution"
                ),
                "task_names": read_only_names,
            })

        return advisories
