# Audit the strictspec adoption (external session hand-off)

An external session gated all seven TOML document-loader families with
strictspec 0.1.0 generated validators (now a declared dependency in six
pyprojects). Committed but NOT released — rides along with the next release.
This todo exists so the work can be audited first.

## What changed and why

Why: seven packages each hand-rolled TOML shape validation with drifting
conventions; strictspec (the fleet's validation authority) now provides one
generated, hard-error document gate per family.

- Schemas + generated validators (committed 444) per family:
  scheduler workflow, agent definition, agent categories, a2a skill,
  tool data-tool (discriminated execution union), overseer knowledge,
  services run-config. `scripts/strictspec_gen.sh` is the regen/freshness
  entry point.
- Each loader validates the document FIRST (hard error), then constructs
  runtime objects natively. Hand-rolled shape checks (~80 lines) deleted;
  SEMANTIC validation (DAG cycles, cross-doc resolution, secret refs,
  uniqueness) deliberately kept native.
- NET-NEW BREAKING: spec documents require integer `format_version = 1`;
  examples and fixtures were stamped; `examples/simple_workflow.toml` was
  additionally completed (missing `timeout`/`context_refinement` previously
  slipped past load and failed later).
- pydantic models were RETAINED as the runtime types (20+ consumers) — the
  strictspec gate is the document contract; pydantic constructs objects.
  Minor redundancy (both reject unknown keys) accepted for now.
- Suite: 3254 passed.

## Audit points and open decisions

1. Two loader families lack dedicated red gate tests (gate-absent /
   unknown-key style) — the external audit flagged the test-coverage gap;
   add them (the other five families have them as templates).
2. OPEN ARCHITECTURE DECISION (deliberately not made): replace the pydantic
   runtime models with strictspec-generated typed dataclasses and route the
   runtime-construction path through them. Touches 20+ consumers; decide
   explicitly, don't drift into it.
3. Pre-existing, unrelated: the releasable config is missing `publish_mode`,
   which errors changelog checks — must be fixed before any release.
