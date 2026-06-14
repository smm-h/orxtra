# Agent Module Design

## Responsibility

Load agent definitions from TOML files. Validate schema. Resolve prompt file references. Apply category mapping. Enforce permissions. This module is purely about loading and validation -- it does not execute agents.

## Agent Definition Format

A `.toml` file with two required sections.

### [agent]

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | string | yes | Unique identifier for the agent. |
| `description` | string | yes | What this agent does. Used in tool descriptions when offered via `consult`. |
| `prompt` | string | yes | Path to .md prompt file, relative to the TOML file's directory. |
| `category` | string | yes | Default category for model selection. No default -- must be explicit. |

### [tools]

| Field | Type | Required | Description |
|---|---|---|---|
| `allow` | array of strings | yes | Tool names this agent CAN use. Whitelist -- must be explicit, even if empty (`[]`). |

This is a pure whitelist. If a tool is not in `allow`, it does not exist from the agent's perspective.

Schema validation is strict (pydantic with `strict=True, extra='forbid'`):
- Unknown keys are errors
- Missing required keys are errors
- Type mismatches are errors

## Permissions -- Mechanical Enforcement

The `allow` list is the sole authority. Before sending a request to the LLM, the executor filters the tool registry to only include tools in the agent's `allow` list.

In `consult` mode (read-only agents), the following tools are mechanically stripped regardless of `allow`:
- File mutation: write, edit, delete, move, copy (destination), mkdir, set_executable
- Execution: exec
- Git mutations: git (mutation subcommands)
- HTTP mutations: http (POST/PUT/DELETE/PATCH stripped, GET/HEAD retained)
- Task lifecycle: start_task, end_task, create_task, create_workflow

## Delegation

Agents delegate work through two mechanisms:

| Mechanism | What it does | Who can use it |
|---|---|---|
| `create_task` / `create_workflow` | Creates structured subtasks within the parent task. Write-capable agents execute them. | Any agent with these tools in its `allow` list. |
| `consult` | Creates a read-only agent session for research. | Any agent with `consult` in its `allow` list. |

All delegation is structured: subtasks nest within the parent task, have pre/post-checks, and failure escalates to the parent.

## Prompt Files

- Live alongside the TOML file as `.md` files
- Can contain `{variable}` placeholders substituted at runtime from task variables
- Simple string substitution only. No Jinja2, no loops, no conditionals.
- Variable substitution is strict both ways: unresolved placeholders and unused variables are hard errors

## Prompt Composition

Agent prompt `.md` files support `{include:filename.md}` for composing prompts from multiple files:

```markdown
# Coding Agent

{include:conventions.md}

## Task

{task}
```

Include resolution:
- Paths relative to the TOML file's directory
- Resolved at load time, before variable substitution
- Nesting supported (included files can include other files)
- Circular includes are hard errors
- Missing includes are hard errors

## Categories

- A flat mapping from intent string to model string
- Loaded from a single `categories.toml` file (consumer provides the path)
- No fallback chains. Missing category is a hard error at load time
- Agents reference categories by name, never by model string

## Loading

```
load_agent(path: Path) -> Agent
load_agents(directory: Path) -> dict[str, Agent]
```

Discovers `*.toml` files in the given directory (non-recursive). Loads each one. Duplicate names are hard errors.

## Files

| File | Contents |
|---|---|
| `_types.py` | `Agent` frozen dataclass / pydantic model. Fields: name, description, prompt (loaded text), category, allow list. |
| `_loader.py` | `load_agent(path)`, `load_agents(directory)`. TOML parsing, pydantic validation, prompt file resolution. |
| `_categories.py` | `load_categories(path)`. `resolve_category(agent, categories)` -- returns model string or raises. |
| `_prompt.py` | `resolve_prompt(template, variables)` -- strict substitution. `resolve_includes(template, base_dir)` -- include resolution with circular detection. |

## What This Module Does NOT Do

- Does not execute agents
- Does not define any built-in agents
- Does not handle model selection beyond category lookup
- Does not validate that tools referenced in `allow` actually exist (that happens at execution time)
