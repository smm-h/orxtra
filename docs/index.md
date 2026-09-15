+++
title = "orxtra"
description = "Structured Orchestration of AI agents as a tree of tasks, against spaghetti vibe-coding -- Task preconditions and postconditions, spend tracking and limits, event logging, etc."
date = 2026-06-16
+++

# orxtra

Structured Orchestration of AI agents as a tree of tasks, against spaghetti vibe-coding -- Task preconditions and postconditions, spend tracking and limits, event logging, etc.

Every piece of work is a task with explicit boundaries, entry conditions (pre-checks), and exit conditions (post-checks). Tasks nest recursively. Failure propagates up the hierarchy.

## CLI Reference

See the [CLI command reference](cli-index/) for all available commands.

## Modules

orxtra is a monorepo with 16 sub-projects across five layers: Foundation (10), Orchestration (2), Intelligence (1), Composition (1), Interfaces (2). Each module is independently useful. A consumer wanting only a typed LLM client uses `orxtra-transport`. One wanting deterministic workflow execution uses `orxtra-scheduler`. The full system composes all 16.
