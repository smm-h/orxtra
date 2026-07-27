# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              skill (format_version 1)
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
    "skill.schema.toml": "name = \"skill\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"Skill\"\ntargets = [\"python\"]\ndescription = \"An A2A skill descriptor: maps an A2A skill id to an orxtra capability, with optional input/output MIME modes.\"\n# Source of truth: orxtra/a2a/_skills.py (SkillRegistry._load_from_toml). id/name/description/\n# capability_name are required; input_modes/output_modes are optional (the loader supplies defaults).\n# Capability EXISTENCE (capability_name resolves to a registered capability) is a cross-document\n# check that stays consumer-native in _load_from_toml.\n\n[types.Skill]\ntype = \"record\"\n[types.Skill.fields.id]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Skill.fields.name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Skill.fields.description]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Skill.fields.capability_name]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Skill.fields.input_modes]\ntype = \"array\"\nrequired = false\n[types.Skill.fields.input_modes.item]\ntype = \"string\"\n[types.Skill.fields.output_modes]\ntype = \"array\"\nrequired = false\n[types.Skill.fields.output_modes.item]\ntype = \"string\"\n",
}
_EMBEDDED_MAIN_FILE = "skill.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[Skill | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[Skill | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_Skill(v), result.diagnostics


def validate_value(v: Value) -> tuple[Skill | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_Skill(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class Skill:
    """Frozen typed binding of the "Skill" record. Immutable; use with_* for
    copy-on-write.
    """

    id: str
    name: str
    description: str
    capability_name: str
    input_modes: list[str]
    output_modes: list[str]

    def with_id(self, v: str) -> Skill:
        return replace(self, id=v)

    def with_name(self, v: str) -> Skill:
        return replace(self, name=v)

    def with_description(self, v: str) -> Skill:
        return replace(self, description=v)

    def with_capability_name(self, v: str) -> Skill:
        return replace(self, capability_name=v)

    def with_input_modes(self, v: list[str]) -> Skill:
        return replace(self, input_modes=v)

    def with_output_modes(self, v: list[str]) -> Skill:
        return replace(self, output_modes=v)


def _bind_Skill(v: Value) -> Skill | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_id = v.field("id")
    f_name = v.field("name")
    f_description = v.field("description")
    f_capability_name = v.field("capability_name")
    f_input_modes = v.field("input_modes")
    f_output_modes = v.field("output_modes")
    return Skill(
        id=(f_id[0].string()[0] if f_id[1] else ""),
        name=(f_name[0].string()[0] if f_name[1] else ""),
        description=(f_description[0].string()[0] if f_description[1] else ""),
        capability_name=(f_capability_name[0].string()[0] if f_capability_name[1] else ""),
        input_modes=([e.string()[0] for e in f_input_modes[0].items()] if f_input_modes[1] else []),
        output_modes=([e.string()[0] for e in f_output_modes[0].items()] if f_output_modes[1] else []),
    )


