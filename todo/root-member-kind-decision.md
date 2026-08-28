# Confirm the root releasable's tag format for rlsbl >= 0.118

## Context

rlsbl 0.118 requires the workspace root member and, for a root-owning
releasable, an explicit `tag_format`. This workspace already declares the
root member correctly (`path = "."`, `name = "root"`,
`releasable = "orxtra"`); only two things block loading under 0.118:

- the member's `watch` key (refused; territory is derived now), and
- the root-owning releasable's missing explicit `tag_format`.

The repository's tags are all `orxtra@v*` and there are no bare version
tags, so the evidently correct declaration is
`tag_format = "{name}@v{version}"` — but a tag format on a root releasable
is deliberately an operator statement, never inferred, which is why the
fleet sweep stopped here.

## The decision

Confirm the format and run:

    uv run python <rlsbl checkout>/scripts/migrate_workspace_model.py \
        --repo . --root-releasable orxtra --tag-format "{name}@v{version}"

(or state a different format). The migration also removes the `watch` key.

## Effort

Minutes once confirmed.
