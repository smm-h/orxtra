# oxtra

A Python library for orchestrating multi-agent AI workflows. Agents are TOML + markdown, tools are Python objects, pipelines are TOML step files. oxtra handles execution, delegation, verification, session management, and cross-agent context sharing.

## Status

Design phase. The directory structure is a scaffold for the Python package. Each module directory has a DESIGN.md that serves as the implementation spec -- it describes the module's architecture, its public API, and a Files section listing the exact Python files to be created with their contents. When implementation begins, the Python files replace the DESIGN.md descriptions. The DESIGN.md files remain as architectural documentation alongside the code.

## Project structure

```
oxtra/
    DESIGN.md              # Architecture, axioms, anti-patterns, examples
    CLAUDE.md
    README.md
    pyproject.toml         # uv-managed, strictcli + rlsbl + selfdoc as dependencies
    selfdoc.json           # selfdoc configuration for generated docs
    .rlsbl/                # rlsbl release scaffolding
        config.json
        changes/
            unreleased.jsonl
    docs/                  # selfdoc templates (_README.md, _CLAUDE.md)
    todo/                  # Work items (active)
        .done/             # Completed items
    scripts/               # Reusable project scripts
    oxtra/
        __init__.py
        agent/             # Agent definition loading and validation
        tool/              # Tool contract, registry, constructors (spawn, consult, notepad, etc.)
        transport/         # LLM communication via Provider protocol
            providers/     # AnthropicProvider, OpenAIProvider
        pipeline/          # Pipeline loading, dependency graph, execution, parallelism
        verify/            # Mechanical (Python callable) + semantic (verification agent) checks
        notepad/           # Append-only JSONL IPC for cross-agent context
        session/           # Session lifecycle, cost tracking, handoff on context limits
        trace/             # Run directory: step results, event logs, session transcripts
    tests/
```

## Key concepts

- **Agents** are TOML + .md files. TOML has name, description, category, and an `allow` tool whitelist. The .md file is the system prompt with `{variable}` placeholders.
- **Tools** are `{name, description, parameters, execute}` objects. oxtra provides constructors (`make_read_tool`, `make_spawn_tool`, etc.) but ships no mandatory tools.
- **Pipelines** are TOML files declaring steps with dependencies, timeouts, retry policy, and verification. The executor builds a dependency graph and runs independent steps in parallel.
- **Categories** map intent strings ("quick", "deep") to model strings ("anthropic/claude-sonnet-4-6") via a flat `categories.toml`. No fallback chains.
- **Providers** implement a 4-method protocol (`build_request`, `parse_response`, `parse_stream`, `extract_usage`). The transport runs a provider-agnostic tool-call loop.

## Tooling

This project uses:

- **rlsbl** for release orchestration, changelog enforcement, and CI scaffolding. Run `rlsbl scaffold` to set up `.rlsbl/`. See the rlsbl protocol in `~/Projects/CLAUDE.md`.
- **strictcli** for the CLI layer (if one is built on top of the library). Schema-driven, no implicit flags.
- **selfdoc** for generated documentation. Templates live in `docs/` (`_README.md`, `_CLAUDE.md`). Generated root files are read-only. Run `selfdoc gen` to regenerate.

## Conventions

- Use `uv` for dependency management, never pip.
- All prompt text lives in .md files, never in Python strings.
- Variable substitution is strict both ways: unresolved placeholders and unused provided variables are both hard errors.
- No implicit defaults for provider, model, timeout, or retry behavior. Missing values are hard errors.
- Every step must declare `depends_on` or `depends_on_previous`. Every agent step must declare `timeout`. `retry_resume` is required when `retry > 0`. `for_each_abort_on_failure` is required when `for_each` is set.
- The trace module is the single owner of the run directory. No other module writes to it directly.
- Tests belong in `tests/`, never in `/tmp/`. One test module per source module.

## Design docs

Each module has a DESIGN.md that serves as the implementation spec. Read it before working on that module. The root DESIGN.md has the full architecture, design axioms, and anti-patterns.
