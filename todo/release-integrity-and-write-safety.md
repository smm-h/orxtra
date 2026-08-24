# Release integrity (0.12.0 is dead on arrival) + write_safety fixes

Found by installing from PyPI into a clean environment (`uv tool install
orxtra`) on 2026-08-05. The published 0.12.0 wheel cannot start: `orxtra
--version` crashes at import. 0.10.1 has also broken retroactively against
current strictcli. Root cause for most of it: the aggregation wheel's
hand-maintained metadata has drifted from the workspace members.

## 1. Aggregate wheel dependency drift (release blocker)

Top-level `pyproject.toml` `dependencies` is maintained by hand while
sub-projects declare their own deps. Two are now missing from the wheel:

- `strictspec` — declared in six member pyprojects, absent at top level →
  `ModuleNotFoundError: strictspec` (via `agent/_gen_categories.py`).
- `pydantic-monty` — `tool/pyproject.toml` pins `==0.0.18`, absent at top
  level → `ModuleNotFoundError: pydantic_monty` (via
  `tool/_data_tool_monty.py`).

Fix the instances, then the class: `hatch_build.py` already parses
workspace members — derive the aggregate dependency list as the union of
member dependencies at build time (excluding intra-workspace `orxtra-*`
refs), or add a CI check that fails when top-level deps ≠ that union.

## 2. `orxtra-cli` dist lookup crashes the aggregate wheel (release blocker)

`cli/_cli.py:127` calls `importlib.metadata.version("orxtra-cli")` at module
scope. In dev installs the `orxtra-cli` dist exists; in the aggregate wheel
only `orxtra` does → `PackageNotFoundError` at import, before any command
runs. Every subpackage `__init__.py` already guards its
`version("orxtra-<member>")` lookup with a try/except fallback — `_cli.py:127`
is the one unguarded call (audited 2026-08-05). Fall back to
`version("orxtra")` (the aggregate name), and add a lint/CI grep that
forbids unguarded `importlib.metadata.version("orxtra-*")` outside the
`__init__` fallback pattern.

## 3. strictcli is unpinned → published releases rot

All pyprojects declare bare `strictcli`. strictcli 0.36.0 reserved the
`quiet` flag name, which retroactively broke orxtra 0.10.1 (`ValueError:
flag name 'quiet' is reserved`) for any fresh install. 0.12.0 happens to be
compatible but only by luck of release timing. Pin a compatible range at
top level (e.g. `strictcli>=0.36,<0.37`) and bump deliberately. Consider
the same for other fast-moving sibling deps (fastware, strictspec).

## 4. Release hygiene

- Yank 0.12.0 on PyPI (unusable) once a fixed release is up; consider
  yanking 0.10.1 or noting the strictcli ceiling in its metadata.
- Add a CI smoke test that builds the wheel, installs it into a clean venv
  (not the workspace), and runs `orxtra --version` + `orxtra --help`. Items
  1–3 were all invisible to the dev-install test suite and trivially caught
  by this.

## 5. write_safety correctness

Current code (identical on main and in the 0.12.0 sdist):

1. Worker and scheduler each construct their own `WriteQueue` +
   `StaleWriteTracker` (`worker/_native.py:215`, `scheduler/_executor.py:242`),
   so their writes to the same path are not serialized against each other
   and staleness state is split. Share one pair per process (inject, don't
   construct). If they can run as separate processes touching the same
   files, that gap is bigger than instance-sharing — decide whether that
   deployment shape is supported and document either the fix or the
   constraint.
2. `_atomic.py`: `mkstemp` temp files are mode 0600 and the rename clobbers
   the target's permissions — `fstat` the target and `fchmod` the temp fd
   before rename. Also missing: `fsync` of the parent directory after
   rename, so the replace isn't crash-durable.
3. `_replay.py`: `ENOSPC` and `EIO` are listed as transient. Disk-full does
   not resolve on a 100 ms backoff and retrying `EIO` hammers failing
   storage. Keep `EINTR`/`EAGAIN`/`EBUSY`/`ENOLCK` class only. Also:
   `with_transient_retry` is exported but not used by `safe_write` — wire it
   in or drop it.
4. `WriteQueue._locks` grows unboundedly (one lock per path, never evicted)
   — refcount and delete when idle. Same for `StaleWriteTracker._reads`
   (per-session entries never GC'd).
5. `WriteQueue.release()` re-resolves the path; if a symlink in the path
   changed between acquire and release, the wrong lock is released. Resolve
   once at acquire and release by that token.
6. Blocking I/O (`os.write`/`fsync`, hashing, `read_text`) runs directly in
   async functions — the `noqa: ASYNC240` sites. Wrap the blocking sections
   in `asyncio.to_thread`.

Item 5.1 is a correctness bug today; 5.2 silently changes file permissions
on every overwrite. The rest are hardening.
