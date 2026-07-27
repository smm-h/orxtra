# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              workflow (format_version 1)
# regenerate:          strictspec gen --manifest strictspec.toml
#
# Released under the MIT license (unencumbered). This file is machine-generated;
# edit the schema and regenerate, never this file.
# ruff: noqa
from __future__ import annotations

from dataclasses import dataclass, replace

import strictspec
from strictspec import Diagnostic, Value

# GENERATED_BY is the strictspec release that produced this file. The runtime
# pairing guard hard-errors unless it matches the linked runtime exactly.
GENERATED_BY = "0.1.0"
SCHEMA_FORMAT_VERSION = 1

# _EMBEDDED_SCHEMA carries the compiled schema (and its imported type-definition
# files and scalar manifest) so the validator is self-contained and does no IO.
_EMBEDDED_SCHEMA = {
    "workflow.schema.toml": "name = \"workflow\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"Workflow\"\ntargets = [\"python\"]\ndescription = \"An orxtra workflow: a recursive task tree with execution-mode selection, conditional requirements, and intra-document dependency references.\"\n# Source of truth: orxtra/scheduler/_loader.py (_parse_task direct_fields, _parse_postchecks,\n# load_workflow) and orxtra/scheduler/_types.py (WorkflowConfig, ServiceConfig, EscalationPolicy),\n# orxtra/protocols Severity. The field/enum surface is the DOCUMENT surface the loader accepts, not\n# the paper draft (examples/orxtra-workflow/schema-workflow.toml), which diverged on enum arms and\n# ServiceConfig shape. Runtime-only TaskSpec fields the loader does not read from documents\n# (orchestrator, execution_target) are intentionally excluded.\n\n[types.Workflow]\ntype = \"record\"\n\n# ---- top-level workflow record ----\n[types.Workflow.fields.workflow]\ntype = \"record\"\nrequired = true\n[types.Workflow.fields.workflow.fields.name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Workflow.fields.workflow.fields.description]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Workflow.fields.workflow.fields.escalation_policy]\ntype = \"enum\"\nrequired = false\nvalues = [\"continue_independent\", \"halt\", \"abort_all\"]\n\n[types.Workflow.fields.tasks]\ntype = \"array\"\nrequired = true\nmin_len = 1                 # \"Workflow must have at least one task\" (_validate_structure)\n[types.Workflow.fields.tasks.item]\ntype = \"Task\"\n\n# Optional top-level dependency map: task_name -> [dep task_names]. The loader merges this into\n# each task's depends_on. Keys and values are intra-document references to task names (resolution\n# of the top-level map to task names stays a consumer-native check in _loader._validate_dependencies).\n[types.Workflow.fields.dependencies]\ntype = \"map\"\nrequired = false\nkey_pattern = \"^[A-Za-z0-9_-]+$\"\norder = \"incidental\"\n[types.Workflow.fields.dependencies.value]\ntype = \"array\"\n[types.Workflow.fields.dependencies.value.item]\ntype = \"string\"\n\n[types.Workflow.fields.services]\ntype = \"array\"\nrequired = false\n[types.Workflow.fields.services.item]\ntype = \"ServiceConfig\"\n\n# ---- reusable execution-check type (prechecks / postchecks) ----\n[types.Execution]\ntype = \"record\"\n[types.Execution.fields.scripts]\ntype = \"array\"\nrequired = false\n[types.Execution.fields.scripts.item]\ntype = \"string\"          # \"module:callable\" references\n[types.Execution.fields.agents]\ntype = \"array\"\nrequired = false\n[types.Execution.fields.agents.item]\ntype = \"record\"\n[types.Execution.fields.agents.item.fields.agent]\ntype = \"string\"\nrequired = true\n[types.Execution.fields.agents.item.fields.task]\ntype = \"string\"\nrequired = true\n[types.Execution.fields.agents.item.fields.block_threshold]\ntype = \"enum\"\nrequired = true\nvalues = [\"critical\", \"major\", \"minor\", \"nit\"]   # orxtra.protocols Severity\n[types.Execution.fields.agents.item.fields.variables]\ntype = \"array\"\nrequired = false\n[types.Execution.fields.agents.item.fields.variables.item]\ntype = \"string\"\n\n# ServiceConfig: orxtra.scheduler._types.ServiceConfig (long-running process declaration).\n[types.ServiceConfig]\ntype = \"record\"\n[types.ServiceConfig.fields.name]\ntype = \"string\"\nrequired = true\n[types.ServiceConfig.fields.start_command]\ntype = \"string\"\nrequired = true\n[types.ServiceConfig.fields.health_check_command]\ntype = \"string\"\nrequired = false\n[types.ServiceConfig.fields.stop_command]\ntype = \"string\"\nrequired = true\n[types.ServiceConfig.fields.port]\ntype = \"integer\"\nrequired = false\n[types.ServiceConfig.fields.ready_timeout]\ntype = \"integer\"\nrequired = false\nmin = 0\n\n# ---- the recursive Task type ----\n[types.Task]\ntype = \"record\"\ndescription = \"A unit of work; may nest via subtasks (recursion, depth-capped).\"\n[types.Task.fields.name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n\n# execution-mode fields (exactly one mode; see constraints below)\n[types.Task.fields.agent]\ntype = \"string\"\nrequired = false\n[types.Task.fields.task_prompt]\ntype = \"string\"\nrequired = false\n[types.Task.fields.callable]\ntype = \"string\"\nrequired = false\n[types.Task.fields.wait_for]\ntype = \"string\"\nrequired = false\n[types.Task.fields.decision_point]\ntype = \"boolean\"\nrequired = false\n[types.Task.fields.subtasks]\ntype = \"array\"\nrequired = false\n[types.Task.fields.subtasks.item]\ntype = \"Task\"            # RECURSIVE self-reference\n\n# agent-mode support fields\n[types.Task.fields.timeout]\ntype = \"integer\"\nrequired = false\nmin = 1\n[types.Task.fields.context_refinement]\ntype = \"boolean\"\nrequired = false\n\n# retry fields\n[types.Task.fields.retry]\ntype = \"integer\"\nrequired = false\nmin = 0\n[types.Task.fields.retry_resume]\ntype = \"boolean\"\nrequired = false\n[types.Task.fields.retry_inject_failure]\ntype = \"boolean\"\nrequired = false\n\n# for_each fields\n[types.Task.fields.for_each]\ntype = \"string\"\nrequired = false\n[types.Task.fields.for_each_abort_on_failure]\ntype = \"boolean\"\nrequired = false\n[types.Task.fields.max_concurrency]\ntype = \"integer\"\nrequired = false\nmin = 1\n\n# misc\n[types.Task.fields.depends_on]\ntype = \"array\"\nrequired = false\n[types.Task.fields.depends_on.item]\ntype = \"string\"          # intra-document references to sibling task names\n[types.Task.fields.variables]\ntype = \"array\"\nrequired = false\n[types.Task.fields.variables.item]\ntype = \"string\"\n[types.Task.fields.category]\ntype = \"string\"\nrequired = false\n[types.Task.fields.output_schema]\ntype = \"string\"\nrequired = false\n[types.Task.fields.budget]\ntype = \"number\"\nrequired = false\nmin = 0\n[types.Task.fields.write_paths]\ntype = \"array\"\nrequired = false\n[types.Task.fields.write_paths.item]\ntype = \"string\"\n[types.Task.fields.on_success]\ntype = \"string\"\nrequired = false\n[types.Task.fields.pre_retry]\ntype = \"string\"\nrequired = false\n[types.Task.fields.prechecks]\ntype = \"Execution\"\nrequired = false\n[types.Task.fields.postchecks]\ntype = \"Execution\"\nrequired = false\n\n# ---- Task constraints (intra-document, phase 2) ----\n\n# Execution-mode selection decomposes cleanly:\n#   co-presence(agent, task_prompt) + exactly-one-of(agent, callable, subtasks, wait_for, decision_point)\n# yields \"exactly one execution mode\" even though agent-mode is a two-field group.\n[[types.Task.constraints]]\nform = \"co-presence\"\nfields = [\"agent\", \"task_prompt\"]\n[[types.Task.constraints]]\nform = \"exactly-one-of\"\nfields = [\"agent\", \"callable\", \"subtasks\", \"wait_for\", \"decision_point\"]\n\n# agent-mode => timeout and context_refinement required (presence-triggered).\n[[types.Task.constraints]]\nform = \"conditional-required\"\nfield = \"timeout\"\nwhen = { field = \"agent\", predicate = \"present\" }\n[[types.Task.constraints]]\nform = \"conditional-required\"\nfield = \"context_refinement\"\nwhen = { field = \"agent\", predicate = \"present\" }\n\n# retry > 0 => retry_resume, retry_inject_failure required. Modeled as retry != 0, which is EXACT\n# only because retry's domain is non-negative (min = 0). Numeric comparison predicates are rejected;\n# the honest origin is `retry != 0`.\n[[types.Task.constraints]]\nform = \"conditional-required\"\nfield = \"retry_resume\"\nwhen = { field = \"retry\", predicate = \"not-equals\", value = 0 }\n[[types.Task.constraints]]\nform = \"conditional-required\"\nfield = \"retry_inject_failure\"\nwhen = { field = \"retry\", predicate = \"not-equals\", value = 0 }\n\n# for_each present => for_each_abort_on_failure, max_concurrency required.\n[[types.Task.constraints]]\nform = \"conditional-required\"\nfield = \"for_each_abort_on_failure\"\nwhen = { field = \"for_each\", predicate = \"present\" }\n[[types.Task.constraints]]\nform = \"conditional-required\"\nfield = \"max_concurrency\"\nwhen = { field = \"for_each\", predicate = \"present\" }\n\n# depends_on entries must resolve to declared task names within the document.\n[[types.Task.constraints]]\nform = \"intra-document-references\"\nreference = \"depends_on\"\nresolves_into = \"tasks\"\nresolves_by = \"field:name\"\n",
}
_EMBEDDED_MAIN_FILE = "workflow.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[Workflow | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[Workflow | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_Workflow(v), result.diagnostics


def validate_value(v: Value) -> tuple[Workflow | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_Workflow(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class Workflow:
    """Frozen typed binding of the "Workflow" record. Immutable; use with_* for
    copy-on-write.
    """

    workflow: Value
    tasks: list[Task]
    dependencies: Value
    services: list[ServiceConfig]

    def with_workflow(self, v: Value) -> Workflow:
        return replace(self, workflow=v)

    def with_tasks(self, v: list[Task]) -> Workflow:
        return replace(self, tasks=v)

    def with_dependencies(self, v: Value) -> Workflow:
        return replace(self, dependencies=v)

    def with_services(self, v: list[ServiceConfig]) -> Workflow:
        return replace(self, services=v)


def _bind_Workflow(v: Value) -> Workflow | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_workflow = v.field("workflow")
    f_tasks = v.field("tasks")
    f_dependencies = v.field("dependencies")
    f_services = v.field("services")
    return Workflow(
        workflow=(f_workflow[0] if f_workflow[1] else Value(None, "json")),
        tasks=([_bind_Task(e) for e in f_tasks[0].items()] if f_tasks[1] else []),
        dependencies=(f_dependencies[0] if f_dependencies[1] else Value(None, "json")),
        services=([_bind_ServiceConfig(e) for e in f_services[0].items()] if f_services[1] else []),
    )


@dataclass(frozen=True, kw_only=True)
class Execution:
    """Frozen typed binding of the "Execution" record. Immutable; use with_* for
    copy-on-write.
    """

    scripts: list[str]
    agents: list[Value]

    def with_scripts(self, v: list[str]) -> Execution:
        return replace(self, scripts=v)

    def with_agents(self, v: list[Value]) -> Execution:
        return replace(self, agents=v)


def _bind_Execution(v: Value) -> Execution | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_scripts = v.field("scripts")
    f_agents = v.field("agents")
    return Execution(
        scripts=([e.string()[0] for e in f_scripts[0].items()] if f_scripts[1] else []),
        agents=([e for e in f_agents[0].items()] if f_agents[1] else []),
    )


@dataclass(frozen=True, kw_only=True)
class ServiceConfig:
    """Frozen typed binding of the "ServiceConfig" record. Immutable; use with_* for
    copy-on-write.
    """

    name: str
    start_command: str
    health_check_command: str
    stop_command: str
    port: int
    ready_timeout: int

    def with_name(self, v: str) -> ServiceConfig:
        return replace(self, name=v)

    def with_start_command(self, v: str) -> ServiceConfig:
        return replace(self, start_command=v)

    def with_health_check_command(self, v: str) -> ServiceConfig:
        return replace(self, health_check_command=v)

    def with_stop_command(self, v: str) -> ServiceConfig:
        return replace(self, stop_command=v)

    def with_port(self, v: int) -> ServiceConfig:
        return replace(self, port=v)

    def with_ready_timeout(self, v: int) -> ServiceConfig:
        return replace(self, ready_timeout=v)


def _bind_ServiceConfig(v: Value) -> ServiceConfig | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_name = v.field("name")
    f_start_command = v.field("start_command")
    f_health_check_command = v.field("health_check_command")
    f_stop_command = v.field("stop_command")
    f_port = v.field("port")
    f_ready_timeout = v.field("ready_timeout")
    return ServiceConfig(
        name=(f_name[0].string()[0] if f_name[1] else ""),
        start_command=(f_start_command[0].string()[0] if f_start_command[1] else ""),
        health_check_command=(f_health_check_command[0].string()[0] if f_health_check_command[1] else ""),
        stop_command=(f_stop_command[0].string()[0] if f_stop_command[1] else ""),
        port=(f_port[0].int()[0] if f_port[1] else 0),
        ready_timeout=(f_ready_timeout[0].int()[0] if f_ready_timeout[1] else 0),
    )


@dataclass(frozen=True, kw_only=True)
class Task:
    """Frozen typed binding of the "Task" record. Immutable; use with_* for
    copy-on-write.
    """

    name: str
    agent: str
    task_prompt: str
    callable: str
    wait_for: str
    decision_point: bool
    subtasks: list[Task]
    timeout: int
    context_refinement: bool
    retry: int
    retry_resume: bool
    retry_inject_failure: bool
    for_each: str
    for_each_abort_on_failure: bool
    max_concurrency: int
    depends_on: list[str]
    variables: list[str]
    category: str
    output_schema: str
    budget: float
    write_paths: list[str]
    on_success: str
    pre_retry: str
    prechecks: Execution | None = None
    postchecks: Execution | None = None

    def with_name(self, v: str) -> Task:
        return replace(self, name=v)

    def with_agent(self, v: str) -> Task:
        return replace(self, agent=v)

    def with_task_prompt(self, v: str) -> Task:
        return replace(self, task_prompt=v)

    def with_callable(self, v: str) -> Task:
        return replace(self, callable=v)

    def with_wait_for(self, v: str) -> Task:
        return replace(self, wait_for=v)

    def with_decision_point(self, v: bool) -> Task:
        return replace(self, decision_point=v)

    def with_subtasks(self, v: list[Task]) -> Task:
        return replace(self, subtasks=v)

    def with_timeout(self, v: int) -> Task:
        return replace(self, timeout=v)

    def with_context_refinement(self, v: bool) -> Task:
        return replace(self, context_refinement=v)

    def with_retry(self, v: int) -> Task:
        return replace(self, retry=v)

    def with_retry_resume(self, v: bool) -> Task:
        return replace(self, retry_resume=v)

    def with_retry_inject_failure(self, v: bool) -> Task:
        return replace(self, retry_inject_failure=v)

    def with_for_each(self, v: str) -> Task:
        return replace(self, for_each=v)

    def with_for_each_abort_on_failure(self, v: bool) -> Task:
        return replace(self, for_each_abort_on_failure=v)

    def with_max_concurrency(self, v: int) -> Task:
        return replace(self, max_concurrency=v)

    def with_depends_on(self, v: list[str]) -> Task:
        return replace(self, depends_on=v)

    def with_variables(self, v: list[str]) -> Task:
        return replace(self, variables=v)

    def with_category(self, v: str) -> Task:
        return replace(self, category=v)

    def with_output_schema(self, v: str) -> Task:
        return replace(self, output_schema=v)

    def with_budget(self, v: float) -> Task:
        return replace(self, budget=v)

    def with_write_paths(self, v: list[str]) -> Task:
        return replace(self, write_paths=v)

    def with_on_success(self, v: str) -> Task:
        return replace(self, on_success=v)

    def with_pre_retry(self, v: str) -> Task:
        return replace(self, pre_retry=v)

    def with_prechecks(self, v: Execution | None) -> Task:
        return replace(self, prechecks=v)

    def with_postchecks(self, v: Execution | None) -> Task:
        return replace(self, postchecks=v)


def _bind_Task(v: Value) -> Task | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_name = v.field("name")
    f_agent = v.field("agent")
    f_task_prompt = v.field("task_prompt")
    f_callable = v.field("callable")
    f_wait_for = v.field("wait_for")
    f_decision_point = v.field("decision_point")
    f_subtasks = v.field("subtasks")
    f_timeout = v.field("timeout")
    f_context_refinement = v.field("context_refinement")
    f_retry = v.field("retry")
    f_retry_resume = v.field("retry_resume")
    f_retry_inject_failure = v.field("retry_inject_failure")
    f_for_each = v.field("for_each")
    f_for_each_abort_on_failure = v.field("for_each_abort_on_failure")
    f_max_concurrency = v.field("max_concurrency")
    f_depends_on = v.field("depends_on")
    f_variables = v.field("variables")
    f_category = v.field("category")
    f_output_schema = v.field("output_schema")
    f_budget = v.field("budget")
    f_write_paths = v.field("write_paths")
    f_on_success = v.field("on_success")
    f_pre_retry = v.field("pre_retry")
    f_prechecks = v.field("prechecks")
    f_postchecks = v.field("postchecks")
    return Task(
        name=(f_name[0].string()[0] if f_name[1] else ""),
        agent=(f_agent[0].string()[0] if f_agent[1] else ""),
        task_prompt=(f_task_prompt[0].string()[0] if f_task_prompt[1] else ""),
        callable=(f_callable[0].string()[0] if f_callable[1] else ""),
        wait_for=(f_wait_for[0].string()[0] if f_wait_for[1] else ""),
        decision_point=(f_decision_point[0].bool()[0] if f_decision_point[1] else False),
        subtasks=([_bind_Task(e) for e in f_subtasks[0].items()] if f_subtasks[1] else []),
        timeout=(f_timeout[0].int()[0] if f_timeout[1] else 0),
        context_refinement=(f_context_refinement[0].bool()[0] if f_context_refinement[1] else False),
        retry=(f_retry[0].int()[0] if f_retry[1] else 0),
        retry_resume=(f_retry_resume[0].bool()[0] if f_retry_resume[1] else False),
        retry_inject_failure=(f_retry_inject_failure[0].bool()[0] if f_retry_inject_failure[1] else False),
        for_each=(f_for_each[0].string()[0] if f_for_each[1] else ""),
        for_each_abort_on_failure=(f_for_each_abort_on_failure[0].bool()[0] if f_for_each_abort_on_failure[1] else False),
        max_concurrency=(f_max_concurrency[0].int()[0] if f_max_concurrency[1] else 0),
        depends_on=([e.string()[0] for e in f_depends_on[0].items()] if f_depends_on[1] else []),
        variables=([e.string()[0] for e in f_variables[0].items()] if f_variables[1] else []),
        category=(f_category[0].string()[0] if f_category[1] else ""),
        output_schema=(f_output_schema[0].string()[0] if f_output_schema[1] else ""),
        budget=(f_budget[0].number()[0] if f_budget[1] else 0.0),
        write_paths=([e.string()[0] for e in f_write_paths[0].items()] if f_write_paths[1] else []),
        on_success=(f_on_success[0].string()[0] if f_on_success[1] else ""),
        pre_retry=(f_pre_retry[0].string()[0] if f_pre_retry[1] else ""),
        prechecks=(_bind_Execution(f_prechecks[0]) if f_prechecks[1] else None),
        postchecks=(_bind_Execution(f_postchecks[0]) if f_postchecks[1] else None),
    )


