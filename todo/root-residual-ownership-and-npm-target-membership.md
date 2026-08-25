# Root residual ownership; npm directory stops being a member

## Background

rlsbl is adopting single-owner workspace attribution: every file has exactly
one owning member (most specific path wins; a mandatory root member owns the
remainder) and the `watch` key is removed. Today this repo's root member owns
files only through its hand-written watch list, because a member with
path "." matches nothing by prefix (an rlsbl defect being fixed). That list
is incomplete — `uv.lock`, `README.md`, and `CHANGELOG.md` are owned by
nobody — and contains a stale glob for a directory that no longer exists
(`knowledge/**`).

Separately, the `npm/` directory is registered as an inert dev-node member
purely because the unregistered-directory check demands registration of any
directory carrying a manifest. In reality it is a secondary release target of
the root releasable: the release pipeline writes its version and publishes it
as the root member's npm job, and the member entry contributes no CI and no
publish jobs — the same directory is modeled twice in the workspace snapshot.
rlsbl is gaining target-path awareness in that check (a directory that is a
declared target path of a registered member is exempt).

## What to consider doing

- Delete the stale `knowledge/**` watch entry now (no effect, pure noise).
- Once rlsbl ships residual root ownership: drop the root member's watch
  list entirely — residual ownership covers everything it listed plus the
  currently-unowned root files.
- Once the target-path-aware check ships: delete the `npm` member entry.
  Nothing changes in how the npm package is versioned or published; the
  double-modeling disappears.

## Why

Residual ownership is strictly better than the hand-maintained list (it
cannot go stale or miss files), and the npm entry exists only to silence a
check that is being taught the real relationship.
