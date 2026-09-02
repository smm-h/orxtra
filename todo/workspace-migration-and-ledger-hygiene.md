# Workspace migration, ledger completeness, stricttest, pricing freshness

## Context

Findings from an external survey of this repository, verified against the
working tree and the installed rlsbl (0.118.1 at filing time). The first two
items are hazards; the rest are consider-at-leisure.

## 1. Migrate workspace.toml to the current rlsbl workspace model (blocking)

rlsbl 0.118.1 refuses the `watch` key on a workspace member at load time
("the 'watch' key is no longer supported"), and this workspace's root member
declares one — so every rlsbl workspace command currently hard-errors
(verified with `rlsbl monorepo list`). Additionally, members declared with
the retired `dev_node = true` spelling need the current form
(`dev_only = true` plus `releasable = false`), and the root member's kind
(dev node vs releasable member) needs the explicit declaration the loader now
requires.

This may partially overlap an existing todo about the root-member decision;
dedup at triage. Until it is done, releases, checks, snapshots, and status
are all blocked.

Affected: `.rlsbl-monorepo/workspace.toml`.
Effort: small.

## 2. Materialize the missing release archives (data-loss hazard)

The releasable's `releases/` directory is missing archives for versions that
have released changelog JSONL files (at filing time: v0.1.0, v0.1.1, v0.1.2,
v0.1.3, and v0.11.0). A bare `rlsbl changelog generate` reads descriptions
from the archives; a version with JSONL but no archive silently loses its
description from CHANGELOG.md and its per-version markdown, auto-committed.
The descriptions are still recoverable from the current CHANGELOG.md today —
materialize the archives before anyone regenerates. Afterwards, run the
anchor backfill (rlsbl repository, `scripts/backfill_release_anchors.py`,
`--dry-run` first) across the full archive set so guarded ledger reads work.

Affected: `.rlsbl-monorepo/releasables/orxtra/releases/`.
Effort: small-medium.

## 3. Adopt the stricttest isolation floor

The pytest configuration carries none of the five required stricttest ini
keys — the fleet's test-isolation floor is not adopted. The suite's
PostgreSQL isolation via testcontainers is already structural (per-test
throwaway databases behind an explicit DSN), which is the part that must stay
structural regardless of socket-guard stances; adoption here is about the
rest of the floor: env hygiene, credential stripping, egress lockdown, and
the sandbox stance. Choose the five stances deliberately (this suite
legitimately needs loopback/container sockets — allowlist them rather than
weakening anything else).

Affected: root `pyproject.toml` `[tool.pytest.ini_options]`, `tests/`
fixtures, possibly CI.
Effort: medium.

## 4. Pricing-table freshness

`session/src/orxtra/session/_pricing.py` hand-maintains per-model USD rates,
and budget enforcement rides on them — a silently stale price makes budget
math wrong with no error anywhere. Consider a declared source plus a
freshness check, or at minimum a structural check (every model referenced by
agents/examples/fixtures resolves in the table, and the table carries a
reviewed-against marker that a check keeps honest). Silent staleness in a
number that controls spending is the exact defect class the fleet's
no-drift-prone-details rule exists for.

Affected: `session/src/orxtra/session/_pricing.py`, a check home (script or
test).
Effort: small-medium.
