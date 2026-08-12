# Experiments as a native first-class concept

## Context

A discipline that has proven itself in agent-driven build campaigns:
**experiment before building**. Any subsystem that rests on an
unverified assumption gets a quick, self-contained experiment first —
its own directory answering exactly ONE question (does library X
behave deterministically across runtimes? does tool Y's pinned
dependency coexist with ours? is configuration Z actually required?),
with probe scripts, captured outputs, and a RESULTS.md carrying a
definitive verdict. The directory is committed, then **frozen**: never
maintained, never wired into CI — it is evidence, not infrastructure.
The conclusion is recorded in the project's decision ledger, and any
finding that must STAY true is additionally promoted into a real test
in the suite.

In practice this repeatedly falsified documentation-derived
assumptions before they became architecture ("artifact inspection
beats documentation"), and it turned would-be mid-build surprises into
cheap, reviewable, permanent evidence.

Today an agent working under orxtra can only do this ad hoc, as
ordinary tasks — nothing structural distinguishes an experiment from
any other work, and nothing enforces the parts that make the
discipline valuable.

## Problem

The discipline's value comes from constraints that are currently pure
convention:

- **One question per experiment** — nothing enforces scope; ad hoc
  experiments sprawl into mini-projects.
- **A definitive verdict** — nothing requires a PASS/FAIL/INCONCLUSIVE
  conclusion to exist before the experiment counts as done.
- **Frozen after conclusion** — nothing prevents later tasks from
  "maintaining" or silently editing concluded experiments, which
  destroys their value as evidence.
- **Conclusion → decision linkage** — the verdict should land in a
  decision record automatically; today the agent must remember.
- **Promotion of must-stay-true findings** — the step where a finding
  becomes a durable test is exactly the step agents skip.
- **Discoverability** — nothing stops a later workflow from re-running
  an experiment whose question was already answered.

orxtra's own philosophy says discipline must be structural, not
agent-goodwill. This is a concrete, well-shaped candidate.

## Options

### A. New `experiment` task type (recommended shape)

A first-class task kind with its own schema: `question` (one string,
mandatory), probe steps, and a mandatory concluding `verdict`
(PASS/FAIL/INCONCLUSIVE) plus evidence paths. Post-check refuses exit
without a verdict and a RESULTS file. On conclusion the system:
records the verdict in the trace and a decision record, freezes the
experiment's directory via write-safety (permanent per-path lock), and
optionally spawns a follow-up task to promote must-stay-true findings
into the test suite.

- Pros: every constraint above becomes structural; the freeze uses
  machinery that already exists (write-safety path locks); verdicts
  become queryable trace data.
- Cons: a new task kind touches schema, scheduler, verification, and
  docs — the widest surface of the three options.

### B. Template + check + Overseer action (lightweight)

No new task kind. Ship an experiment directory template, a reusable
post-check ("RESULTS verdict exists and is well-formed"), and an
Overseer action `record_experiment_conclusion` that writes the
decision record and applies the freeze.

- Pros: small; composes from existing pieces; can ship quickly and
  inform option A later.
- Cons: one-question scoping and promotion remain conventions; nothing
  marks a task AS an experiment, so discoverability stays weak.

### C. A plus an experiment registry

Option A plus a queryable registry of concluded experiments
(question, verdict, date, evidence path), with an Overseer/agent query
surface: "has this question been answered?" Pre-check on new
experiment tasks warns/errors on near-duplicate questions.

- Pros: closes the re-running hole; turns accumulated evidence into an
  institutional memory.
- Cons: largest scope; near-duplicate question matching is fuzzy and
  needs a deliberate design (exact-match-only is an honest v1).

## Affected areas (tentative — verify against current code)

- task schema + scheduler (new kind or template wiring)
- `verify/` (verdict post-check)
- `write-safety/` (permanent freeze locks on concluded directories)
- `overseer/` action vocabulary (conclusion/decision recording)
- `trace/` (verdicts as structured events)
- docs + `examples/`

## Effort

- B: small (days)
- A: medium (roughly a week)
- C: A plus registry design — medium-large

A reasonable path: B first to validate the shape, then A/C promoting
it to a real task kind once usage patterns are visible.
