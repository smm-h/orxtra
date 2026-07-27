# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              categories (format_version 1)
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
    "categories.schema.toml": "name = \"categories\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"Categories\"\ntargets = [\"python\"]\ndescription = \"orxtra category-to-model mapping: a [categories] table of category name -> 'provider/model' string.\"\n# Source of truth: orxtra/agent/_categories.py (load_categories). The loader returns data[\"categories\"]\n# as a dict[str, str]. Cross-document resolution (agent.category must appear here) stays consumer-native\n# in resolve_category.\n\n[types.Categories]\ntype = \"record\"\n[types.Categories.fields.categories]\ntype = \"map\"\nrequired = true\nkey_pattern = \"^[A-Za-z0-9_.-]+$\"\norder = \"incidental\"\n[types.Categories.fields.categories.value]\ntype = \"string\"\nnon_empty = true\n",
}
_EMBEDDED_MAIN_FILE = "categories.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[Categories | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[Categories | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_Categories(v), result.diagnostics


def validate_value(v: Value) -> tuple[Categories | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_Categories(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class Categories:
    """Frozen typed binding of the "Categories" record. Immutable; use with_* for
    copy-on-write.
    """

    categories: Value

    def with_categories(self, v: Value) -> Categories:
        return replace(self, categories=v)


def _bind_Categories(v: Value) -> Categories | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_categories = v.field("categories")
    return Categories(
        categories=(f_categories[0] if f_categories[1] else Value(None, "json")),
    )


