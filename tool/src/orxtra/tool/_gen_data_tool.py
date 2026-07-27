# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              data_tool (format_version 1)
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
    "data_tool.schema.toml": "name = \"data_tool\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"DataTool\"\ntargets = [\"python\"]\ndescription = \"A data-defined tool TOML: [tool] identity, optional [params], a discriminated [execution] block (http/monty/command), and an optional [output] JSON-Schema.\"\n# Source of truth: orxtra/tool/_data_tool_types.py (DataToolDefinition, ParamDef, ResourceLimits,\n# HttpExecution, MontyExecution, CommandExecution, OutputConfig) and _data_tool_loader.py. The\n# execution block is a discriminated union on `type`. The [output] `schema` is an arbitrary JSON\n# Schema dict (a declared blind spot). Secret-reference validation stays consumer-native.\n\n[types.DataTool]\ntype = \"record\"\n\n[types.DataTool.fields.tool]\ntype = \"ToolIdentity\"\nrequired = true\n[types.DataTool.fields.params]\ntype = \"map\"\nrequired = false\nkey_pattern = \"^[A-Za-z_][A-Za-z0-9_]*$\"\norder = \"incidental\"\n[types.DataTool.fields.params.value]\ntype = \"ParamDef\"\n[types.DataTool.fields.execution]\ntype = \"discriminated-union\"\nrequired = true\ndiscriminator = \"type\"\n[types.DataTool.fields.execution.arms.http]\ntype = \"HttpExecution\"\n[types.DataTool.fields.execution.arms.monty]\ntype = \"MontyExecution\"\n[types.DataTool.fields.execution.arms.command]\ntype = \"CommandExecution\"\n[types.DataTool.fields.output]\ntype = \"OutputConfig\"\nrequired = false\n\n[types.ToolIdentity]\ntype = \"record\"\ndescription = \"The [tool] identity block.\"\n[types.ToolIdentity.fields.name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.ToolIdentity.fields.description]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.ToolIdentity.fields.namespace]\ntype = \"string\"\nrequired = true\nregex = \"^custom\\\\.\"\n[types.ToolIdentity.fields.deferred]\ntype = \"boolean\"\nrequired = true\n[types.ToolIdentity.fields.tags]\ntype = \"array\"\nrequired = false\n[types.ToolIdentity.fields.tags.item]\ntype = \"string\"\n\n[types.ParamDef]\ntype = \"record\"\n[types.ParamDef.fields.type]\ntype = \"enum\"\nrequired = true\nvalues = [\"string\", \"integer\", \"number\", \"boolean\"]\n[types.ParamDef.fields.description]\ntype = \"string\"\nrequired = true\n[types.ParamDef.fields.required]\ntype = \"boolean\"\nrequired = true\n[types.ParamDef.fields.pattern]\ntype = \"string\"\nrequired = false\n\n[types.HttpExecution]\ntype = \"record\"\n[types.HttpExecution.fields.type]\ntype = \"literal\"\nvalue = \"http\"\n[types.HttpExecution.fields.method]\ntype = \"enum\"\nrequired = true\nvalues = [\"GET\", \"HEAD\", \"POST\", \"PUT\", \"DELETE\", \"PATCH\"]\n[types.HttpExecution.fields.url]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.HttpExecution.fields.headers]\ntype = \"map\"\nrequired = false\nkey_pattern = \"^.+$\"\norder = \"incidental\"\n[types.HttpExecution.fields.headers.value]\ntype = \"string\"\n[types.HttpExecution.fields.body_template]\ntype = \"string\"\nrequired = false\n\n[types.MontyExecution]\ntype = \"record\"\n[types.MontyExecution.fields.type]\ntype = \"literal\"\nvalue = \"monty\"\n[types.MontyExecution.fields.code]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.MontyExecution.fields.capabilities]\ntype = \"array\"\nrequired = true\n[types.MontyExecution.fields.capabilities.item]\ntype = \"string\"\n[types.MontyExecution.fields.limits]\ntype = \"ResourceLimits\"\nrequired = true\n\n[types.ResourceLimits]\ntype = \"record\"\n[types.ResourceLimits.fields.max_duration_secs]\ntype = \"integer\"\nrequired = true\n[types.ResourceLimits.fields.max_allocations]\ntype = \"integer\"\nrequired = false\n[types.ResourceLimits.fields.max_memory]\ntype = \"integer\"\nrequired = false\n\n[types.CommandExecution]\ntype = \"record\"\n[types.CommandExecution.fields.type]\ntype = \"literal\"\nvalue = \"command\"\n[types.CommandExecution.fields.executable]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.CommandExecution.fields.arg_validation]\ntype = \"boolean\"\nrequired = true\n[types.CommandExecution.fields.timeout_ceiling]\ntype = \"integer\"\nrequired = true\n\n[types.OutputConfig]\ntype = \"record\"\n[types.OutputConfig.fields.schema]\ntype = \"opaque\"\nrequired = true\nunchecked = true\nunchecked_reason = \"The output schema is an arbitrary JSON Schema dict validated by jsonschema at tool-execution time, not structurally here.\"\n",
}
_EMBEDDED_MAIN_FILE = "data_tool.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[DataTool | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[DataTool | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_DataTool(v), result.diagnostics


def validate_value(v: Value) -> tuple[DataTool | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_DataTool(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class DataTool:
    """Frozen typed binding of the "DataTool" record. Immutable; use with_* for
    copy-on-write.
    """

    tool: ToolIdentity
    params: Value
    execution: Value
    output: OutputConfig | None = None

    def with_tool(self, v: ToolIdentity) -> DataTool:
        return replace(self, tool=v)

    def with_params(self, v: Value) -> DataTool:
        return replace(self, params=v)

    def with_execution(self, v: Value) -> DataTool:
        return replace(self, execution=v)

    def with_output(self, v: OutputConfig | None) -> DataTool:
        return replace(self, output=v)


def _bind_DataTool(v: Value) -> DataTool | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_tool = v.field("tool")
    f_params = v.field("params")
    f_execution = v.field("execution")
    f_output = v.field("output")
    return DataTool(
        tool=(_bind_ToolIdentity(f_tool[0]) if f_tool[1] else None),
        params=(f_params[0] if f_params[1] else Value(None, "json")),
        execution=(f_execution[0] if f_execution[1] else Value(None, "json")),
        output=(_bind_OutputConfig(f_output[0]) if f_output[1] else None),
    )


@dataclass(frozen=True, kw_only=True)
class ToolIdentity:
    """Frozen typed binding of the "ToolIdentity" record. Immutable; use with_* for
    copy-on-write.
    """

    name: str
    description: str
    namespace: str
    deferred: bool
    tags: list[str]

    def with_name(self, v: str) -> ToolIdentity:
        return replace(self, name=v)

    def with_description(self, v: str) -> ToolIdentity:
        return replace(self, description=v)

    def with_namespace(self, v: str) -> ToolIdentity:
        return replace(self, namespace=v)

    def with_deferred(self, v: bool) -> ToolIdentity:
        return replace(self, deferred=v)

    def with_tags(self, v: list[str]) -> ToolIdentity:
        return replace(self, tags=v)


def _bind_ToolIdentity(v: Value) -> ToolIdentity | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_name = v.field("name")
    f_description = v.field("description")
    f_namespace = v.field("namespace")
    f_deferred = v.field("deferred")
    f_tags = v.field("tags")
    return ToolIdentity(
        name=(f_name[0].string()[0] if f_name[1] else ""),
        description=(f_description[0].string()[0] if f_description[1] else ""),
        namespace=(f_namespace[0].string()[0] if f_namespace[1] else ""),
        deferred=(f_deferred[0].bool()[0] if f_deferred[1] else False),
        tags=([e.string()[0] for e in f_tags[0].items()] if f_tags[1] else []),
    )


@dataclass(frozen=True, kw_only=True)
class ParamDef:
    """Frozen typed binding of the "ParamDef" record. Immutable; use with_* for
    copy-on-write.
    """

    type: str
    description: str
    required: bool
    pattern: str

    def with_type(self, v: str) -> ParamDef:
        return replace(self, type=v)

    def with_description(self, v: str) -> ParamDef:
        return replace(self, description=v)

    def with_required(self, v: bool) -> ParamDef:
        return replace(self, required=v)

    def with_pattern(self, v: str) -> ParamDef:
        return replace(self, pattern=v)


def _bind_ParamDef(v: Value) -> ParamDef | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_type = v.field("type")
    f_description = v.field("description")
    f_required = v.field("required")
    f_pattern = v.field("pattern")
    return ParamDef(
        type=(f_type[0].string()[0] if f_type[1] else ""),
        description=(f_description[0].string()[0] if f_description[1] else ""),
        required=(f_required[0].bool()[0] if f_required[1] else False),
        pattern=(f_pattern[0].string()[0] if f_pattern[1] else ""),
    )


@dataclass(frozen=True, kw_only=True)
class HttpExecution:
    """Frozen typed binding of the "HttpExecution" record. Immutable; use with_* for
    copy-on-write.
    """

    type: str
    method: str
    url: str
    headers: Value
    body_template: str

    def with_type(self, v: str) -> HttpExecution:
        return replace(self, type=v)

    def with_method(self, v: str) -> HttpExecution:
        return replace(self, method=v)

    def with_url(self, v: str) -> HttpExecution:
        return replace(self, url=v)

    def with_headers(self, v: Value) -> HttpExecution:
        return replace(self, headers=v)

    def with_body_template(self, v: str) -> HttpExecution:
        return replace(self, body_template=v)


def _bind_HttpExecution(v: Value) -> HttpExecution | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_type = v.field("type")
    f_method = v.field("method")
    f_url = v.field("url")
    f_headers = v.field("headers")
    f_body_template = v.field("body_template")
    return HttpExecution(
        type=(f_type[0].string()[0] if f_type[1] else ""),
        method=(f_method[0].string()[0] if f_method[1] else ""),
        url=(f_url[0].string()[0] if f_url[1] else ""),
        headers=(f_headers[0] if f_headers[1] else Value(None, "json")),
        body_template=(f_body_template[0].string()[0] if f_body_template[1] else ""),
    )


@dataclass(frozen=True, kw_only=True)
class MontyExecution:
    """Frozen typed binding of the "MontyExecution" record. Immutable; use with_* for
    copy-on-write.
    """

    type: str
    code: str
    capabilities: list[str]
    limits: ResourceLimits

    def with_type(self, v: str) -> MontyExecution:
        return replace(self, type=v)

    def with_code(self, v: str) -> MontyExecution:
        return replace(self, code=v)

    def with_capabilities(self, v: list[str]) -> MontyExecution:
        return replace(self, capabilities=v)

    def with_limits(self, v: ResourceLimits) -> MontyExecution:
        return replace(self, limits=v)


def _bind_MontyExecution(v: Value) -> MontyExecution | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_type = v.field("type")
    f_code = v.field("code")
    f_capabilities = v.field("capabilities")
    f_limits = v.field("limits")
    return MontyExecution(
        type=(f_type[0].string()[0] if f_type[1] else ""),
        code=(f_code[0].string()[0] if f_code[1] else ""),
        capabilities=([e.string()[0] for e in f_capabilities[0].items()] if f_capabilities[1] else []),
        limits=(_bind_ResourceLimits(f_limits[0]) if f_limits[1] else None),
    )


@dataclass(frozen=True, kw_only=True)
class ResourceLimits:
    """Frozen typed binding of the "ResourceLimits" record. Immutable; use with_* for
    copy-on-write.
    """

    max_duration_secs: int
    max_allocations: int
    max_memory: int

    def with_max_duration_secs(self, v: int) -> ResourceLimits:
        return replace(self, max_duration_secs=v)

    def with_max_allocations(self, v: int) -> ResourceLimits:
        return replace(self, max_allocations=v)

    def with_max_memory(self, v: int) -> ResourceLimits:
        return replace(self, max_memory=v)


def _bind_ResourceLimits(v: Value) -> ResourceLimits | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_max_duration_secs = v.field("max_duration_secs")
    f_max_allocations = v.field("max_allocations")
    f_max_memory = v.field("max_memory")
    return ResourceLimits(
        max_duration_secs=(f_max_duration_secs[0].int()[0] if f_max_duration_secs[1] else 0),
        max_allocations=(f_max_allocations[0].int()[0] if f_max_allocations[1] else 0),
        max_memory=(f_max_memory[0].int()[0] if f_max_memory[1] else 0),
    )


@dataclass(frozen=True, kw_only=True)
class CommandExecution:
    """Frozen typed binding of the "CommandExecution" record. Immutable; use with_* for
    copy-on-write.
    """

    type: str
    executable: str
    arg_validation: bool
    timeout_ceiling: int

    def with_type(self, v: str) -> CommandExecution:
        return replace(self, type=v)

    def with_executable(self, v: str) -> CommandExecution:
        return replace(self, executable=v)

    def with_arg_validation(self, v: bool) -> CommandExecution:
        return replace(self, arg_validation=v)

    def with_timeout_ceiling(self, v: int) -> CommandExecution:
        return replace(self, timeout_ceiling=v)


def _bind_CommandExecution(v: Value) -> CommandExecution | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_type = v.field("type")
    f_executable = v.field("executable")
    f_arg_validation = v.field("arg_validation")
    f_timeout_ceiling = v.field("timeout_ceiling")
    return CommandExecution(
        type=(f_type[0].string()[0] if f_type[1] else ""),
        executable=(f_executable[0].string()[0] if f_executable[1] else ""),
        arg_validation=(f_arg_validation[0].bool()[0] if f_arg_validation[1] else False),
        timeout_ceiling=(f_timeout_ceiling[0].int()[0] if f_timeout_ceiling[1] else 0),
    )


@dataclass(frozen=True, kw_only=True)
class OutputConfig:
    """Frozen typed binding of the "OutputConfig" record. Immutable; use with_* for
    copy-on-write.
    """

    schema: Value

    def with_schema(self, v: Value) -> OutputConfig:
        return replace(self, schema=v)


def _bind_OutputConfig(v: Value) -> OutputConfig | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_schema = v.field("schema")
    return OutputConfig(
        schema=(f_schema[0] if f_schema[1] else Value(None, "json")),
    )


