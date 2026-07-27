# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              knowledge (format_version 1)
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
    "knowledge.schema.toml": "name = \"knowledge\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"KnowledgeFile\"\ntargets = [\"python\"]\ndescription = \"An orxtra knowledge constraint file: a list of [[constraints]] with text, tier, and kind. Loaded into the run's constraint memory.\"\n# Source of truth: orxtra/overseer/_knowledge.py (_load_toml). The loader reads data[\"constraints\"]\n# and requires text/tier/kind on each. tier/kind VALUES are not validated by the loader (passed\n# through to write_constraint), so they are required strings here, not enums.\n\n[types.KnowledgeFile]\ntype = \"record\"\n[types.KnowledgeFile.fields.constraints]\ntype = \"array\"\nrequired = false\n[types.KnowledgeFile.fields.constraints.item]\ntype = \"Constraint\"\n\n[types.Constraint]\ntype = \"record\"\n[types.Constraint.fields.text]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Constraint.fields.tier]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.Constraint.fields.kind]\ntype = \"string\"\nrequired = true\nnon_empty = true\n",
}
_EMBEDDED_MAIN_FILE = "knowledge.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[KnowledgeFile | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[KnowledgeFile | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_KnowledgeFile(v), result.diagnostics


def validate_value(v: Value) -> tuple[KnowledgeFile | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_KnowledgeFile(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class KnowledgeFile:
    """Frozen typed binding of the "KnowledgeFile" record. Immutable; use with_* for
    copy-on-write.
    """

    constraints: list[Constraint]

    def with_constraints(self, v: list[Constraint]) -> KnowledgeFile:
        return replace(self, constraints=v)


def _bind_KnowledgeFile(v: Value) -> KnowledgeFile | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_constraints = v.field("constraints")
    return KnowledgeFile(
        constraints=([_bind_Constraint(e) for e in f_constraints[0].items()] if f_constraints[1] else []),
    )


@dataclass(frozen=True, kw_only=True)
class Constraint:
    """Frozen typed binding of the "Constraint" record. Immutable; use with_* for
    copy-on-write.
    """

    text: str
    tier: str
    kind: str

    def with_text(self, v: str) -> Constraint:
        return replace(self, text=v)

    def with_tier(self, v: str) -> Constraint:
        return replace(self, tier=v)

    def with_kind(self, v: str) -> Constraint:
        return replace(self, kind=v)


def _bind_Constraint(v: Value) -> Constraint | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_text = v.field("text")
    f_tier = v.field("tier")
    f_kind = v.field("kind")
    return Constraint(
        text=(f_text[0].string()[0] if f_text[1] else ""),
        tier=(f_tier[0].string()[0] if f_tier[1] else ""),
        kind=(f_kind[0].string()[0] if f_kind[1] else ""),
    )


