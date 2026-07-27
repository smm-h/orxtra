# strictspec generated validator. DO NOT EDIT.
#
# strictspec generator: 0.1.0
# schema:              run_config (format_version 1)
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
    "run_config.schema.toml": "name = \"run_config\"\nmeta_version = 1\nformat_version = 1\ndocument_syntax = \"toml\"\nrole = \"schema\"\nroot = \"RunConfig\"\ntargets = [\"python\"]\ndescription = \"An orxtra run configuration file for start_run_from_file: paths, db_url, provider configs, budget, and autonomy policy.\"\n# Source of truth: orxtra/services/_run.py (RunConfig, start_run_from_file). In the DOCUMENT, the\n# *_path/*_dir fields are strings (the loader coerces them to Path afterward) and budget is a string\n# (the loader coerces it to Decimal via Decimal(str(...)), the money-precision convention).\n\n[types.RunConfig]\ntype = \"record\"\n[types.RunConfig.fields.workflow_path]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.agents_dir]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.knowledge_dir]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.categories_path]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.read_root]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.db_url]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.provider_configs]\ntype = \"map\"\nrequired = true\nkey_pattern = \"^[A-Za-z0-9_.-]+$\"\norder = \"incidental\"\n[types.RunConfig.fields.provider_configs.value]\ntype = \"map\"\nkey_pattern = \"^[A-Za-z0-9_.-]+$\"\norder = \"incidental\"\n[types.RunConfig.fields.provider_configs.value.value]\ntype = \"string\"\n[types.RunConfig.fields.budget]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.autonomy_level]\ntype = \"string\"\nrequired = true\nnon_empty = true\n[types.RunConfig.fields.budget_exhaustion_policy]\ntype = \"enum\"\nrequired = false\nvalues = [\"block_new\", \"cancel_all\", \"timeout_grace\", \"unlimited\"]\n[types.RunConfig.fields.secrets_env]\ntype = \"map\"\nrequired = false\nkey_pattern = \"^.+$\"\norder = \"incidental\"\n[types.RunConfig.fields.secrets_env.value]\ntype = \"string\"\n[types.RunConfig.fields.tools_dir]\ntype = \"string\"\nrequired = false\nnon_empty = true\n",
}
_EMBEDDED_MAIN_FILE = "run_config.schema.toml"

# Version pairing: generated code and runtime must be the same release. This runs
# at import, so a skewed runtime hard-errors before any validation is attempted.
strictspec.require_runtime_version(GENERATED_BY)
_program = strictspec.compile_embedded(_EMBEDDED_SCHEMA, _EMBEDDED_MAIN_FILE)


def validate_bytes(input: bytes, syntax: str) -> tuple[RunConfig | None, tuple[Diagnostic, ...]]:
    """RAW-BYTES entry point: lossless parse of input in the given syntax
    ("json" | "toml" | "jsonl"), then validate. Returns the typed root value
    (None when any diagnostic fired) and the ordered diagnostics.
    """
    return validate_bytes_with_evidence(input, syntax, None)


def validate_bytes_with_evidence(input: bytes, syntax: str, evidence: dict | None) -> tuple[RunConfig | None, tuple[Diagnostic, ...]]:
    """validate_bytes plus cross-document resolver evidence for the phase-2
    constraint vocabulary.
    """
    result = _program.validate_with_evidence(input, syntax, evidence)
    if not result.valid:
        return None, result.diagnostics
    v = strictspec.load_value(input, syntax)
    return _bind_RunConfig(v), result.diagnostics


def validate_value(v: Value) -> tuple[RunConfig | None, tuple[Diagnostic, ...]]:
    """TAGGED-VALUE entry point: validate an already-parsed tagged document value
    (from strictspec.load_value or a typed constructor). Raw untagged dicts are
    never accepted.
    """
    result = _program.validate_value(v)
    if not result.valid:
        return None, result.diagnostics
    return _bind_RunConfig(v), result.diagnostics


@dataclass(frozen=True, kw_only=True)
class RunConfig:
    """Frozen typed binding of the "RunConfig" record. Immutable; use with_* for
    copy-on-write.
    """

    workflow_path: str
    agents_dir: str
    knowledge_dir: str
    categories_path: str
    read_root: str
    db_url: str
    provider_configs: Value
    budget: str
    autonomy_level: str
    budget_exhaustion_policy: str
    secrets_env: Value
    tools_dir: str

    def with_workflow_path(self, v: str) -> RunConfig:
        return replace(self, workflow_path=v)

    def with_agents_dir(self, v: str) -> RunConfig:
        return replace(self, agents_dir=v)

    def with_knowledge_dir(self, v: str) -> RunConfig:
        return replace(self, knowledge_dir=v)

    def with_categories_path(self, v: str) -> RunConfig:
        return replace(self, categories_path=v)

    def with_read_root(self, v: str) -> RunConfig:
        return replace(self, read_root=v)

    def with_db_url(self, v: str) -> RunConfig:
        return replace(self, db_url=v)

    def with_provider_configs(self, v: Value) -> RunConfig:
        return replace(self, provider_configs=v)

    def with_budget(self, v: str) -> RunConfig:
        return replace(self, budget=v)

    def with_autonomy_level(self, v: str) -> RunConfig:
        return replace(self, autonomy_level=v)

    def with_budget_exhaustion_policy(self, v: str) -> RunConfig:
        return replace(self, budget_exhaustion_policy=v)

    def with_secrets_env(self, v: Value) -> RunConfig:
        return replace(self, secrets_env=v)

    def with_tools_dir(self, v: str) -> RunConfig:
        return replace(self, tools_dir=v)


def _bind_RunConfig(v: Value) -> RunConfig | None:
    if v.kind() != strictspec.Kind.RECORD:
        return None
    f_workflow_path = v.field("workflow_path")
    f_agents_dir = v.field("agents_dir")
    f_knowledge_dir = v.field("knowledge_dir")
    f_categories_path = v.field("categories_path")
    f_read_root = v.field("read_root")
    f_db_url = v.field("db_url")
    f_provider_configs = v.field("provider_configs")
    f_budget = v.field("budget")
    f_autonomy_level = v.field("autonomy_level")
    f_budget_exhaustion_policy = v.field("budget_exhaustion_policy")
    f_secrets_env = v.field("secrets_env")
    f_tools_dir = v.field("tools_dir")
    return RunConfig(
        workflow_path=(f_workflow_path[0].string()[0] if f_workflow_path[1] else ""),
        agents_dir=(f_agents_dir[0].string()[0] if f_agents_dir[1] else ""),
        knowledge_dir=(f_knowledge_dir[0].string()[0] if f_knowledge_dir[1] else ""),
        categories_path=(f_categories_path[0].string()[0] if f_categories_path[1] else ""),
        read_root=(f_read_root[0].string()[0] if f_read_root[1] else ""),
        db_url=(f_db_url[0].string()[0] if f_db_url[1] else ""),
        provider_configs=(f_provider_configs[0] if f_provider_configs[1] else Value(None, "json")),
        budget=(f_budget[0].string()[0] if f_budget[1] else ""),
        autonomy_level=(f_autonomy_level[0].string()[0] if f_autonomy_level[1] else ""),
        budget_exhaustion_policy=(f_budget_exhaustion_policy[0].string()[0] if f_budget_exhaustion_policy[1] else ""),
        secrets_env=(f_secrets_env[0] if f_secrets_env[1] else Value(None, "json")),
        tools_dir=(f_tools_dir[0].string()[0] if f_tools_dir[1] else ""),
    )


