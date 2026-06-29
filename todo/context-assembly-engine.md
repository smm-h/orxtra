# Generalized context assembly engine for AI agents

## Problem

AI agent context injection is fragmented across tools and frameworks. Each tool has its own format (CLAUDE.md, AGENTS.md, .cursorrules, .windsurfrules), each framework has its own composition model, and none of them solve the fundamental problem: tools should ship their own agent-facing context, and a system should assemble it.

Today, users manually write and maintain instructions about how agents should use their tools. This doesn't scale. Tool authors know best how agents should interact with their tools -- the same way CLI tools ship `--help` and man pages. But there's no standard mechanism for:
- Tools declaring agent context as part of their package
- A runtime discovering and assembling that context
- Priority ordering and composition across multiple sources
- Hook composition with explicit semantics (not just string concatenation)
- Cross-tool delivery (the same assembled context serving CC, OpenCode, Cursor, etc.)

## Vision

A context package manager for AI agents. Three provider layers, each with different ownership:

### Provider layers

1. **Tool-shipped**: Tools (release tooling, safe deletion, safe git, etc.) ship their own context and hooks as part of their packages. When a tool updates, the context updates. The tool author maintains it. Discovery via `importlib.resources` (Python), `exports` in package.json (npm), or `embed` (Go).

2. **User-authored**: Personal workflow preferences, coding style, agent discipline rules. Things no tool can know. Lives in a user-level config directory.

3. **Project-authored**: Project-specific instructions. Lives in the repo.

### What each provider ships (beyond markdown)

- **System prompt fragments**: The current use case -- text injected into the agent's system prompt
- **Pre-prompt hooks**: Run before each agent turn (validation, context refresh)
- **Post-prompt hooks**: Run after each agent turn (commit checks, format enforcement)
- **MCP server declarations**: A tool can ship an MCP server config fragment (transport type, command, args, env) that the assembly engine composes into the agent runtime's MCP configuration. For CC, this means generating or merging into `.mcp.json`. For OpenCode, merging into the `mcp` section of `opencode.json`. The tool declares the server; the engine wires it into whichever runtime is active.
- **Tool declarations**: CLI wrappers, custom tool definitions the agent should know about
- **Constraints**: Hard rules the agent must follow with the tool (enforced, not advisory)

### Multi-channel delivery

Different agent runtimes accept context through different channels, and a single runtime may have multiple channels with different characteristics:

- **System prompt channel**: Static, session-scoped, benefits from prompt caching. Best for stable tool knowledge.
- **Messages/context channel**: Per-turn, dynamic, no caching benefit. Best for state-dependent context (current branch, active tasks).
- **Hook-injected channel**: Middleware that runs per-event, can inject context conditionally. Best for reactive context (inject lint rules only when editing code).
- **MCP/tool registration channel**: Structural, not textual. Registers capabilities the agent can invoke.

The assembly engine should be channel-aware: each fragment declares which channel(s) it targets. A tool might ship a system prompt fragment (how to use it), a hook (validation before each turn), and an MCP server config (its runtime API) -- all as part of a single context package.

### Tag-based composition

- Each project declares tags (explicit or auto-detected from project signals)
- Tags resolve to context fragments via an implication graph (tag A implies tag B)
- Fragments are priority-ordered and composed deterministically
- The system is a DAG, not a tree -- cross-cutting concerns compose naturally

### Cross-tool delivery

The assembled context must be deliverable to multiple agent runtimes:
- CC: via `--append-system-prompt` flag
- OpenCode: via `AGENTS.md` file generation, plugin system, or `instructions` config key
- Cursor: via `.cursor/rules/*.mdc` generation
- Generic: via AGENTS.md (the emerging cross-tool standard, 60k+ repos, Linux Foundation governance)

The assembly engine is tool-agnostic. Delivery adapters translate the assembled context into each tool's native format.

### Hook composition model

Lessons from existing hook systems (git 2.54, tapable, systemd, pre-commit, WordPress):

- **Named relative ordering beats numeric priority** for independently-authored hooks (self-documenting, survives changes)
- **Explicit hook types**: void (fire-and-forget), bail (first non-null wins), waterfall (chain through), parallel (all concurrently). The hook creator chooses the composition model, not the subscriber.
- **Soft/hard failure distinction**: some hooks are advisory (failure logged), others are mandatory (failure blocks). Configured per-hook, not globally.
- **Timeouts per hook**: no existing hook system provides this, and it's a universal gap. Every real-world deployment needs it.
- **Introspection**: `context list`, `context graph`, `context hooks` to see what's registered and in what order.

### Evergreen updates

Each tool-shipped context fragment has a version. The system tracks what version it last assembled. On launch, it checks if the installed tool has a newer context version and pulls it. Same as a package lock -- reproducibility with explicit upgrade points.

### Security

The TrapDoor attack (May 2026) showed 34 malicious packages planting context files with zero-width Unicode hidden instructions. If the system auto-loads tool-shipped context from installed packages, it inherits this attack surface. Mitigations:
- Content hashing with allowlist
- Review-on-first-load (show diff, require explicit approval)
- Unicode normalization and hidden character stripping
- Signed context fragments (tool author signs, system verifies)

### Token budget awareness

- Individual fragments should stay under ~200 lines (adherence drops past that)
- Total assembled context should stay under ~30K tokens for quality
- The system should track total assembled token count and warn/error when it exceeds thresholds
- Progressive disclosure: only name+description loaded at startup, full content loaded on demand (mirrors the Agent Skills SKILL.md pattern)

## Industry context

- **AGENTS.md**: Cross-tool standard (60k+ repos, 30+ tools, Linux Foundation AAIF). Plain markdown, no schema. CC notably does NOT read it.
- **Agent Skills / SKILL.md**: Anthropic-originated standard for task capabilities. Progressive disclosure. ~670k skills, 40+ tools.
- **MCP**: Runtime tool connectivity (JSON-RPC). Does not address static context packaging.
- **Next.js 16.2**: Ships version-matched docs in packages. Vercel evals: 100% pass rate with bundled docs vs 53% baseline.
- **TanStack Intent, antfu/skills-npm**: npm packages shipping Agent Skills alongside code.
- No formal standard exists at the registry level (no PyPI/npm metadata field for agent context).

The gap nobody has formally addressed: "library X ships version-matched AI instructions inside its package, discoverable via a standard manifest field." This system fills that gap.

## Relationship to existing modules

The context assembly engine could be:
- A new foundation-layer module (e.g., `context/`) with zero intra-workspace deps
- Used by the agent module for agent definition loading (agents already use TOML+md)
- Used by the tool module for tool-level context injection
- Used by the Overseer for dynamic context assembly
- Exposed via CLI and MCP for external consumption

The agent definition system already loads TOML+md files with prompt composition. The context assembly engine generalizes this pattern to arbitrary context sources with a richer composition model.

## Effort

Large. This is a multi-session project spanning:
- Core assembly engine (fragment discovery, DAG resolution, deterministic composition): 2-3 sessions
- Tool-shipped context protocol (discovery, versioning, caching): 2-3 sessions
- Hook composition framework (types, ordering, failure semantics, timeouts): 3-4 sessions
- Delivery adapters (CC, OpenCode, AGENTS.md, Cursor): 2-3 sessions
- Security layer (hashing, signing, review-on-first-load): 2-3 sessions
- Token budget tracking and progressive disclosure: 1-2 sessions
- CLI and introspection commands: 1-2 sessions
- Tests: 3-4 sessions
