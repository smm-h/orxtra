# orxt/agent -- Design

## Responsibility

Load agent definitions from TOML files. Validate schema. Resolve prompt file references. Apply category mapping. Enforce permissions. This module is purely about loading and validation -- it does not execute agents.

## Agent Definition Format

A `.toml` file with two required sections.

### [agent]

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier for the agent. |
| `description` | string | yes | What this agent does. Used in tool descriptions when the agent is offered via `spawn` or `consult`. |
| `prompt` | string | yes | Path to .md prompt file, relative to the TOML file's directory. |
| `category` | string | yes | Default category for model selection (e.g., "quick", "deep"). No default -- must be explicit. |

### [tools]

| Field | Type | Required | Description |
|---|---|---|---|
| `allow` | array of strings | yes | Tool names this agent CAN use. Whitelist -- if a tool is not listed, the agent cannot use it. Must be explicit, even if empty (`[]`). |

This is a pure whitelist. There is no deny list. If a tool is not in `allow`, it does not exist from the agent's perspective.

`spawn` receives special treatment: it is **mechanically stripped** from the tool set for all spawned agents, regardless of what `allow` says. Even if an agent's TOML lists `spawn` in `allow`, the scheduler removes it when spawning that agent as a worker. Only the scheduler itself can invoke `spawn`. This prevents orchestration recursion without requiring the agent author to remember to omit it.

Schema validation is strict (pydantic with `strict=True, extra='forbid'`):
- Unknown keys are errors. No extra fields allowed.
- Missing required keys are errors. No defaults filled in.
- Type mismatches are errors.

## Prompt Files

- Live alongside the TOML file as `.md` files.
- Can contain `{variable}` placeholders that get substituted at runtime from workflow step variables.
- Simple string substitution only. No Jinja2, no loops, no conditionals, no filters.
- Read once at agent load time, substituted at spawn time.
- Variable substitution is strict both ways. Unresolved placeholders are hard errors. Unused provided variables are also hard errors. The template and the variable set must match exactly.

## Prompt Composition

Agent prompt `.md` files support an `{include:filename.md}` directive for composing prompts from multiple files:

```markdown
# Coding Agent

You are a code generation agent. Follow the project conventions below.

{include:conventions.md}

## Framework API

{include:framework-api.md}

## Task

{task}
```

Include resolution:
- Paths are relative to the TOML file's directory
- Includes are resolved at load time, before variable substitution
- Nesting is supported (included files can include other files)
- Circular includes are hard errors (detected via path tracking)
- A missing include target is a hard error

`{include:...}` directives are syntactically distinct from `{variable}` placeholders -- they use a colon after `include`.

## Categories

- A flat mapping from intent string to model string.
- Loaded from a single `categories.toml` file at the project root.
- No fallback chains. If a category referenced by an agent does not exist in the map, hard error at load time.
- Agents reference categories by name, never by model string. The TOML schema rejects model strings in the `category` field.
- Workflow steps can override the agent's default category per-invocation.

## Permissions -- Mechanical Enforcement

The `allow` list is the sole authority. Before sending a request to the LLM, the executor filters the tool registry to only include tools in the agent's `allow` list. The agent never sees tools outside its whitelist.

Special cases:
- `spawn` is mechanically stripped from all spawned agents, regardless of `allow`.
- `consult` (read-only agent invocation) follows the normal whitelist rule.
- `notepad` follows the normal whitelist rule.

## Two-Tier Delegation

| Level | Description | Who can use it | Agent capabilities |
|---|---|---|---|
| `spawn` | Creates a full agent session with write access. | Scheduler only. Mechanically stripped from all spawned agents. | Full tool access per the spawned agent's `allow` list. |
| `consult` | Creates a read-only agent session. | Any agent with `consult` in its `allow` list. | Cannot use write/edit/delete/move/mkdir/set_executable/spawn/git-mutations/exec tools. For research, not execution. |

## Loading

```
load_agent(path: Path) -> Agent
```
Reads a single TOML file, validates schema, resolves prompt path, reads prompt content. Returns an `Agent` data object.

```
load_agents(directory: Path) -> dict[str, Agent]
```
Discovers all `*.toml` files in the given directory (non-recursive). Loads each one. Returns a dict keyed by agent name. Duplicate names are hard errors.

No version field in agent definitions. Agent format changes are breaking changes to orxt.

## Files

| File | Contents |
|---|---|
| `_types.py` | `Agent` frozen dataclass / pydantic model. Fields mirror the TOML schema: name, description, prompt (loaded text), category, allow list. |
| `_loader.py` | `load_agent(path)`, `load_agents(directory)`. TOML parsing, pydantic validation, prompt file resolution. |
| `_categories.py` | `load_categories(path)` -- reads `categories.toml`, returns `dict[str, str]`. `resolve_category(agent, categories)` -- looks up the agent's category, returns model string or raises. |
| `_prompt.py` | `resolve_prompt(template, variables)` -- `{variable}` substitution with strict-both-ways validation. `resolve_includes(template, base_dir)` -- `{include:filename.md}` resolution with circular detection. |

## What This Module Does NOT Do

- Does not execute agents.
- Does not define any built-in agents.
- Does not handle model selection beyond category lookup.
- Does not parse or interpret prompt content beyond placeholder substitution.
- Does not validate that tools referenced in `allow` actually exist (that happens at workflow execution time).
