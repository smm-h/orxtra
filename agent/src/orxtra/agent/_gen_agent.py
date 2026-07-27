# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              agent (format_version 1)
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
    "agent.schema.toml": "name = \"agent\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"AgentDef\"\ntargets = [\"python\"]\ndescription = \"An orxtra agent definition: [agent] identity/routing plus a [tools] allow-list and optional inline [[tools.define]] tool declarations.\"\n# Source of truth: orxtra/agent/_loader.py (load_agent) and orxtra/agent/_types.py (Agent,\n# InlineToolDefinition). In the DOCUMENT, `prompt` is a path (relative to the file) that the loader\n# resolves and replaces with the composed prompt text. Inline-tool `params`/`execution`/`output` are\n# opaque at agent-load time: they are validated later against the DataToolDefinition schema at build\n# time (the loader defers them), so this schema treats them as declared blind spots.\n\n[types.AgentDef]\ntype = \"record\"\n\n[types.AgentDef.fields.agent]\ntype = \"AgentIdentity\"\nrequired = true\n\n[types.AgentIdentity]\ntype = \"record\"\ndescription = \"The [agent] identity/routing block.\"\n[types.AgentIdentity.fields.name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.AgentIdentity.fields.description]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.AgentIdentity.fields.prompt]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.AgentIdentity.fields.category]\ntype = \"string\"\nrequired = false\n[types.AgentIdentity.fields.provider]\ntype = \"string\"\nrequired = false\n[types.AgentIdentity.fields.model]\ntype = \"string\"\nrequired = false\n[types.AgentIdentity.fields.budget]\ntype = \"number\"\nrequired = false\nmin = 0\n[types.AgentIdentity.fields.write_paths]\ntype = \"array\"\nrequired = false\n[types.AgentIdentity.fields.write_paths.item]\ntype = \"string\"\n[types.AgentIdentity.fields.timeout]\ntype = \"integer\"\nrequired = false\nmin = 1\n\n# ---- routing constraints (Agent._validate_routing) ----\n# category XOR (provider AND model), and at least one of the two forms must be present.\n[[types.AgentIdentity.constraints]]\nform = \"co-presence\"\nfields = [\"provider\", \"model\"]\n[[types.AgentIdentity.constraints]]\nform = \"mutual-exclusion\"\nfields = [\"category\", \"provider\"]\n[[types.AgentIdentity.constraints]]\nform = \"mutual-exclusion\"\nfields = [\"category\", \"model\"]\n[[types.AgentIdentity.constraints]]\nform = \"at-least-one-of\"\nfields = [\"category\", \"provider\"]\n\n[types.AgentDef.fields.tools]\ntype = \"record\"\nrequired = true\n[types.AgentDef.fields.tools.fields.allow]\ntype = \"array\"\nrequired = true\n[types.AgentDef.fields.tools.fields.allow.item]\ntype = \"string\"\n[types.AgentDef.fields.tools.fields.deferred]\ntype = \"array\"\nrequired = false\n[types.AgentDef.fields.tools.fields.deferred.item]\ntype = \"string\"\n[types.AgentDef.fields.tools.fields.define]\ntype = \"array\"\nrequired = false\n[types.AgentDef.fields.tools.fields.define.item]\ntype = \"InlineTool\"\n\n[types.InlineTool]\ntype = \"record\"\ndescription = \"An inline [[tools.define]] tool declaration. Shape-checked here; full param/execution/output validation is deferred to the DataToolDefinition schema at build time.\"\n[types.InlineTool.fields.name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.InlineTool.fields.description]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.InlineTool.fields.namespace]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.InlineTool.fields.deferred]\ntype = \"boolean\"\nrequired = true\n[types.InlineTool.fields.tags]\ntype = \"array\"\nrequired = false\n[types.InlineTool.fields.tags.item]\ntype = \"string\"\n[types.InlineTool.fields.params]\ntype = \"opaque\"\nrequired = false\nunchecked = true\nunchecked_reason = \"Inline-tool params are validated at build time against the DataToolDefinition schema, not at agent load.\"\n[types.InlineTool.fields.execution]\ntype = \"opaque\"\nrequired = true\nunchecked = true\nunchecked_reason = \"Inline-tool execution is validated at build time against the DataToolDefinition schema, not at agent load.\"\n[types.InlineTool.fields.output]\ntype = \"opaque\"\nrequired = false\nunchecked = true\nunchecked_reason = \"Inline-tool output is validated at build time against the DataToolDefinition schema, not at agent load.\"\n",
}
_EMBEDDED_MAIN_FILE = "agent.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[AgentDef | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[AgentDef | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_AgentDef(v), result.diagnostics


def validate_value(v: Value) -> tuple[AgentDef | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_AgentDef(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class AgentDef:
    """Frozen typed binding of the "AgentDef" record. Immutable; use with_* for
    copy-on-write.
    """

    agent: AgentIdentity
    tools: Value

    def with_agent(self, v: AgentIdentity) -> AgentDef:
        return replace(self, agent=v)

    def with_tools(self, v: Value) -> AgentDef:
        return replace(self, tools=v)


def _bind_AgentDef(v: Value) -> AgentDef | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_agent = v.field("agent")
    f_tools = v.field("tools")
    return AgentDef(
        agent=(_bind_AgentIdentity(f_agent[0]) if f_agent[1] else None),
        tools=(f_tools[0] if f_tools[1] else Value(None, "json")),
    )


@dataclass(frozen=True, kw_only=True)
class AgentIdentity:
    """Frozen typed binding of the "AgentIdentity" record. Immutable; use with_* for
    copy-on-write.
    """

    name: str
    description: str
    prompt: str
    category: str
    provider: str
    model: str
    budget: float
    write_paths: list[str]
    timeout: int

    def with_name(self, v: str) -> AgentIdentity:
        return replace(self, name=v)

    def with_description(self, v: str) -> AgentIdentity:
        return replace(self, description=v)

    def with_prompt(self, v: str) -> AgentIdentity:
        return replace(self, prompt=v)

    def with_category(self, v: str) -> AgentIdentity:
        return replace(self, category=v)

    def with_provider(self, v: str) -> AgentIdentity:
        return replace(self, provider=v)

    def with_model(self, v: str) -> AgentIdentity:
        return replace(self, model=v)

    def with_budget(self, v: float) -> AgentIdentity:
        return replace(self, budget=v)

    def with_write_paths(self, v: list[str]) -> AgentIdentity:
        return replace(self, write_paths=v)

    def with_timeout(self, v: int) -> AgentIdentity:
        return replace(self, timeout=v)


def _bind_AgentIdentity(v: Value) -> AgentIdentity | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_name = v.field("name")
    f_description = v.field("description")
    f_prompt = v.field("prompt")
    f_category = v.field("category")
    f_provider = v.field("provider")
    f_model = v.field("model")
    f_budget = v.field("budget")
    f_write_paths = v.field("write_paths")
    f_timeout = v.field("timeout")
    return AgentIdentity(
        name=(f_name[0].string()[0] if f_name[1] else ""),
        description=(f_description[0].string()[0] if f_description[1] else ""),
        prompt=(f_prompt[0].string()[0] if f_prompt[1] else ""),
        category=(f_category[0].string()[0] if f_category[1] else ""),
        provider=(f_provider[0].string()[0] if f_provider[1] else ""),
        model=(f_model[0].string()[0] if f_model[1] else ""),
        budget=(f_budget[0].number()[0] if f_budget[1] else 0.0),
        write_paths=([e.string()[0] for e in f_write_paths[0].items()] if f_write_paths[1] else []),
        timeout=(f_timeout[0].int()[0] if f_timeout[1] else 0),
    )


@dataclass(frozen=True, kw_only=True)
class InlineTool:
    """Frozen typed binding of the "InlineTool" record. Immutable; use with_* for
    copy-on-write.
    """

    name: str
    description: str
    namespace: str
    deferred: bool
    tags: list[str]
    params: Value
    execution: Value
    output: Value

    def with_name(self, v: str) -> InlineTool:
        return replace(self, name=v)

    def with_description(self, v: str) -> InlineTool:
        return replace(self, description=v)

    def with_namespace(self, v: str) -> InlineTool:
        return replace(self, namespace=v)

    def with_deferred(self, v: bool) -> InlineTool:
        return replace(self, deferred=v)

    def with_tags(self, v: list[str]) -> InlineTool:
        return replace(self, tags=v)

    def with_params(self, v: Value) -> InlineTool:
        return replace(self, params=v)

    def with_execution(self, v: Value) -> InlineTool:
        return replace(self, execution=v)

    def with_output(self, v: Value) -> InlineTool:
        return replace(self, output=v)


def _bind_InlineTool(v: Value) -> InlineTool | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_name = v.field("name")
    f_description = v.field("description")
    f_namespace = v.field("namespace")
    f_deferred = v.field("deferred")
    f_tags = v.field("tags")
    f_params = v.field("params")
    f_execution = v.field("execution")
    f_output = v.field("output")
    return InlineTool(
        name=(f_name[0].string()[0] if f_name[1] else ""),
        description=(f_description[0].string()[0] if f_description[1] else ""),
        namespace=(f_namespace[0].string()[0] if f_namespace[1] else ""),
        deferred=(f_deferred[0].bool()[0] if f_deferred[1] else False),
        tags=([e.string()[0] for e in f_tags[0].items()] if f_tags[1] else []),
        params=(f_params[0] if f_params[1] else Value(None, "json")),
        execution=(f_execution[0] if f_execution[1] else Value(None, "json")),
        output=(f_output[0] if f_output[1] else Value(None, "json")),
    )


