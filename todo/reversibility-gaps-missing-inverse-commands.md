# Reversibility gap: `db migrate` has no rollback

## Context

strictcli is gaining declared reversibility support: a mutating command will
declare which command undoes it (verified at registration in both
directions), a command with no recovery will declare irreversible with a
mandatory reason, a warn-severity check will flag destructive commands
declaring neither, and after a real run the framework will print a paste-able
recovery command and emit a machine-readable recovery member in the JSON
result document. When this repo adopts that support, the gap below needs
either a built inverse or an honest irreversible declaration.

## Problem

The `db migrate` group has `plan`, `status`, and `apply` — no `rollback`. An
applied migration that should not have been applied is recovered by hand.

## Effort

Medium — depends on whether the migration layer records enough to reverse an
apply, or whether rollback is declared irreversible with a reason (restore
from backup being the honest recovery path).
