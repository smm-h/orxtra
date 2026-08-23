# Batch file-edit scripts need preview-first primitives

## Context

The monty data-tool system (capability-scoped sandboxed scripts defined as
validated TOML files) is the intended home for repeatable multi-file
mechanical operations: the capability table already provides `read`,
`grep`, `glob`, `edit`, `multi_edit`, `write`, and the mutating
capabilities route through the real tools (path containment, write scope,
audited deletion). Operational experience with agent-driven bulk edits
elsewhere shows the failure mode of ad-hoc scripting is always the same:
the script writes first and verifies after — no dry run, no
expected-count assertion, silent no-ops on mistyped patterns, silent
half-application on batches.

The governing discipline such operations need: careful, dry-run capable,
the dry run runs FIRST, its output is examined, and only then does the
real run execute. The data-tool layer covers the safety half of that
today (no shell escape; `edit` hard-errors on zero matches) but has no
preview half at all.

## Problem

Five concrete gaps, each verified against the current source:

1. **No preview mode at the tool layer.** `--dry-run` exists only as the
   CLI framework flag at the command boundary; no `fs.write`-family tool,
   no capability host function, and no tool-definition field can record a
   would-do change instead of performing it. `_MUTATION_CAPABILITIES`
   already classifies every capability as mutating or not — nothing
   consumes that classification to suppress effects.
2. **No proposed-content diff.** The `diff` capability compares two files
   on disk. Previewing an edit would require writing a candidate file —
   which is the mutation the preview exists to avoid. A
   string-vs-file form (`diff_proposed(path, new_content)`) is missing.
3. **No read-only occurrence count.** A script cannot assert an expected
   match count before mutating; `edit` reports its count only after
   writing, as prose. A `count(path, needle)` primitive turns
   "print N afterwards" into "refuse unless exactly N".
4. **`replace_all` is not exposed on the `edit` capability.** The host
   function signature is `edit_file(path, old_string, new_string)`; a
   file with multiple identical occurrences is a hard error with no way
   to say yes. A single-file N-occurrence replacement — the most common
   bulk-edit shape — is therefore inexpressible via `edit`, and scripts
   fall back to raw `read` + in-script replace + `write`, losing the
   zero-match refusal and the count report exactly where they are needed
   most.
5. **`multi_edit`'s per-edit zero-match is a soft failure.** Where single
   `edit` raises, `multi_edit` appends "Edit {i}: old_string not found"
   to a failure list and continues, so a mechanical batch can silently
   half-apply with the loss reported in a text blob. For batch renames
   that is the wrong severity; a hard error (or an explicit
   `on_missing = "error" | "skip"` per edit record) is needed.

## Solutions

- **1 (preview):** a preview switch plumbed into the mutating host
  functions — record a structured effect (path, match count,
  before/after excerpt) instead of performing it, keyed off the
  classification that already exists. Pro: one mechanism, every mutating
  capability inherits it; mirrors the framework-level dry-run shape one
  layer down. Con: touches every mutating tool's signature or a shared
  execution seam.
- **2 (diff_proposed):** new read-only capability. Pro: small, enables
  real unified-diff previews. Con: none apparent.
- **3 (count):** new read-only capability. Pro: trivially small; makes
  assert-before-mutate the natural script shape. Con: none apparent.
- **4 (replace_all):** add the flag to the `edit` host function and
  capability schema. Pro: removes the incentive to route around `edit`.
  Con: a footgun if used reflexively — mitigated by 1-3 (preview + count
  first).
- **5 (multi_edit severity):** default per-edit misses to hard error;
  allow `skip` only as an explicit per-edit declaration. Pro: batches
  cannot half-apply silently. Con: breaking change for any existing
  tool relying on the soft behavior.

Items 2-3 are pure additions; 1 is the substantial one; 4-5 are
correctness fixes independent of preview.

## Affected

- `tool/src/orxtra/tool/_data_tool_monty.py` (capability table, host
  function signatures)
- `tool/src/orxtra/tool/_write_tools.py` (edit/multi_edit semantics,
  preview recording)
- `tool/src/orxtra/tool/_read_tools.py` (count, diff_proposed)
- `tool/src/orxtra/tool/_data_tool_types.py` (tool-definition surface if
  preview is declarable per tool)
- docs/configuration.md (the data-tool section)

## Effort

Medium. Items 2-4 are each small; item 5 is small plus a compatibility
decision; item 1 is the real work (a record-instead-of-perform seam
through the mutating host functions plus a structured effects report).
