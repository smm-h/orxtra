# A tool install of cli/ alone produces a broken CLI — the root distribution must be paired in

## Context

`orxtra.cli.__version__` reads distribution metadata for the AGGREGATE
distribution (`version("orxtra")` — the root pyproject's publishable wheel
that force-includes every workspace member at build time). The cli member's
own pyproject documents the hazard: syncing the member alone yields the code
without the distribution it reads.

## Problem

`uv tool install -e <repo>/cli` — the natural single-path install an
automated editable-install repair pass will run — produces a CLI that dies at
startup with `importlib.metadata.PackageNotFoundError: No package metadata
was found for orxtra`. This happened in practice during a machine-wide
editable-reinstall pass: the tool venv was rebuilt from `cli/` alone and the
CLI broke until the root was paired in.

The working install requires two parts:

    uv tool install --force --editable <repo>/cli --with-editable <repo>

(The root's editable build deliberately materializes zero files, so pairing
it in supplies metadata only and cannot shadow the members.)

There is no single-path invocation that works: installing the root alone is
hollow (the workspace members live in the root's dev dependency-group, which
tool installs do not include, and the editable build injects no member code).

## Solutions

1. **Move the workspace members from the root's dev group into its runtime
   dependencies.** Then `uv tool install -e <repo>` works standalone: the
   root supplies its own metadata and resolves every member editable.
   Pros: one canonical install path; automated reinstall passes just work;
   matches what the aggregate wheel already promises at build time.
   Cons: changes the root's published dependency surface — verify the wheel
   build (which force-includes sources) does not end up double-declaring
   members, and that CI syncs stay correct.
2. **Stop reading the root's metadata from the cli member.** Let
   `orxtra.cli.__version__` read its own distribution (`version("orxtra-cli")`)
   or a generated version constant. Pros: cli/ becomes standalone-installable;
   smallest change. Cons: the CLI then reports the member's version rather
   than the aggregate's — decide whether those are meant to stay in lockstep
   (the workspace versioning may already guarantee it).
3. **Encode the two-part install as the project's own command** (a documented
   script or the release tooling's dev-install configuration), so no caller
   ever composes it by hand. Pros: no dependency-surface change.
   Cons: automated generic reinstall passes still break the venv until they
   learn the project-specific command — the failure recurs by design.

Options 1 and 2 remove the failure structurally; option 3 only documents it.

## Affected files

- root `pyproject.toml` (option 1) or `cli/src/orxtra/cli/__init__.py`
  (option 2)
- `hatch_build.py` interactions (option 1 verification)
- the cli member pyproject's hazard note (update whichever way this resolves)

## Effort

Small for option 2; small-plus-verification for option 1.
