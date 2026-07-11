# `orchestrator` silently unsettable from workflow TOML

## Context

Found during a code audit (2026-07) of the workflow-config loading path.

## Problem

The `orchestrator` field cannot actually be set from a workflow TOML file — a value supplied
in the file is silently ignored rather than applied or rejected. The apparent cause: the field
is declared with `dataclasses.field(default_factory=...)` on a **pydantic** model. Pydantic
does not honor stdlib-dataclasses field descriptors, so the declaration behaves as an opaque
default and the config value never binds.

This is a silent-failure bug class: the user writes valid-looking config, gets no error, and
the setting has no effect.

## Suggested fix

1. Reproduce: write a workflow TOML that sets `orchestrator`, load it, assert the value took
   effect (red test first).
2. Replace the stdlib `dataclasses.field(...)` declaration with the pydantic-native equivalent
   (`Field(default_factory=...)`) or restructure the model.
3. Audit sibling models for the same pattern — `dataclasses.field` on any pydantic model is
   the same latent bug.
4. Consider rejecting unknown/unbound keys at load time so a value that cannot bind is a hard
   error rather than a silent no-op.

## Affected area

Workflow TOML loading / the model declaring `orchestrator`.

## Effort

Small for the single field (test + one-line fix); the sibling-pattern audit is the real work
(grep for `dataclasses.field` under pydantic models).
