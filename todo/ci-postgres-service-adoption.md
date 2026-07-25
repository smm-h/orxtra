# CI: real-Postgres integration tests silently skip — adopt declarative services

Filed 2026-07-24.

## Problem

The Postgres-backed integration tests (testcontainers-based fixtures gated by a
docker-availability skip) silently skip in CI: the CI workflows provision no
Docker/Postgres service, so the skip guard always fires and the DB integration
surface is never exercised on push. Green CI therefore overstates coverage —
the exact silent-degradation pattern the toolchain philosophy forbids.

## Solution now available

rlsbl now supports declarative CI service containers: a `services` map (image,
ports, env, health, optional in-container setup commands + verification) plus a
`test_env` map in `.rlsbl/config.json`, rendered into the per-target test CI
workflow by `rlsbl scaffold`, with a `requires-services` check that hard-errors
when declared services aren't provisioned in the on-disk workflow.

## Work

1. Declare the Postgres service (+ any required extensions via the setup block)
   and the connection/require env vars in `.rlsbl/config.json`.
2. Point the test fixtures at the CI-provided DSN via the env convention
   (project-prefixed DB env vars) so CI connects to the service instead of
   skipping; keep local-dev behavior unchanged.
3. Re-run `rlsbl scaffold`; verify the DB tests actually execute in CI (no
   skips) and `rlsbl check --name requires-services` passes.

## Effort

Small-medium: config authoring + fixture env wiring + one scaffold run + CI
verification.
