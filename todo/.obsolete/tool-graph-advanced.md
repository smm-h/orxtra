# Advanced tool graph: edges, inference, and semantic discovery

## Context

The MVP tool discovery infrastructure is implemented. This covers the organizational layer (metadata, filtering, deferred loading) that makes large tool sets manageable. What remains is the relational and semantic layer from the original vision.

### What was built

- **Tool model** (`protocols._tool.Tool`): `namespace` (str, hierarchical dot-separated), `tags` (frozenset), `deferred` (bool) fields on the frozen dataclass.
- **Hierarchical namespaces**: built-in tools organized into `fs.read`, `fs.write`, `io.notepad`, `io.http`, `exec`, `meta.consult`. Namespace wildcards (`fs.*`) match the prefix and all children.
- **Capability tags**: `readonly`, `mutation` tags on all built-in tools. Tag filters (`#readonly`) in allow lists select all tools with that tag.
- **Allow-list wildcards and tags**: `resolve_allow_list` in `scheduler._allow_resolver` supports explicit names, namespace wildcards (`fs.*`), tag filters (`#readonly`), and universal wildcard (`*`).
- **ToolRegistry**: `scheduler._tool_registry.ToolRegistry` with `ToolEntry` (name, namespace, tags, factory), `register`/`register_custom`/`get_metadata`/`build_tools`. `create_builtin_registry` populates it. `_build_agent_tools` in `_agent_execution` uses the registry for data-driven tool construction.
- **load_tools meta-tool**: `tool._load_tools.make_load_tools_tool` lets agents request full schemas on demand during a session, backed by `Session.update_tools()`.
- **Provider-aware deferred loading**: Anthropic (`defer_loading: true`), OpenAI (empty `parameters` with description hint), Gemini (omits `parameters` entirely). Each provider's formatter handles the `deferred` flag natively.

### What the original todo envisioned but was not built

The original `tool-graph-discovery.md` described four approaches: tool graph, capability tags, hierarchical namespaces, and contextual filtering. Namespaces and tags are done. The remaining approaches are the relational ones.

## Remaining work

### 1. Graph edges (depends-on, follows, related-to)

Typed relationships between tools: `deploy_staging` depends-on `check_run`, follows `git_push`, is related-to `get_deploy_status`. When an agent uses a tool, the graph surfaces adjacent tools that become available via `load_tools`.

Open questions:
- Edge types: `depends_on` (hard prerequisite), `follows` (common sequence), `related_to` (same domain). Are these sufficient?
- Storage: edges on ToolEntry? Separate adjacency structure? Edges need to be queryable by source tool, target tool, and edge type.
- Static vs dynamic: built-in tool relationships are static (defined at registration). Custom/consumer tool relationships could be static (declared in TOML) or dynamic (inferred).

### 2. Trace-based relationship inference

Derive tool relationships from historical usage. If agents consistently call `git_status` then `git_diff` then `edit`, those tools have an inferred `follows` relationship. This addresses the cold-start problem for custom tools and discovers relationships that nobody declared.

Dependencies:
- Trace analysis infrastructure does not exist yet. The trace module stores events but has no query/analysis layer for aggregating tool co-occurrence patterns.
- Needs enough historical data to be meaningful. Inference quality depends on volume and diversity of traces.
- Must handle noise: two tools appearing in the same session is not the same as two tools appearing in a consistent sequence.

### 3. Semantic discovery (embedding search)

Given a natural-language query ("I need to deploy to staging"), find tools whose descriptions are semantically close. This is the most flexible discovery mechanism but requires embedding infrastructure.

Open questions:
- Embedding provider: use the same LLM transport layer? Separate embedding-specific provider?
- Index: precompute embeddings for all tool descriptions at registry construction time? Recompute when tools change?
- Threshold: how to determine when a semantic match is "close enough" without silently degrading to "give everything"?

### 4. Contextual pre-filtering (task spec analysis)

The scheduler analyzes a task's description, pre-checks, and post-checks to automatically select relevant tools before the agent starts. No manual allow-list needed for well-specified tasks.

Open questions:
- Requires an LLM call to analyze the task spec, which adds latency and cost before the agent even starts.
- The filter decision is opaque -- debugging why an agent did not get a tool it needed is harder.
- Interaction with allow lists: does contextual filtering replace allow lists, narrow them, or provide a default when no allow list is specified?

## Dependencies

- **Trace analysis infrastructure**: does not exist. Required for approach 2. The trace module owns PG schema and event storage, but has no aggregation or query layer for tool co-occurrence analysis.
- **strictcli bridge** (separate todo): would bring CLI tools into the tool registry. More tools in the registry makes the graph more valuable and the discovery problem more pressing.

## Effort

- Graph edges (static): moderate. Data model + registration API + adjacency queries + load_tools integration.
- Trace-based inference: large. Requires trace query layer, statistical analysis, confidence thresholds, incremental updates.
- Semantic discovery: moderate-to-large. Embedding provider integration, index management, similarity search.
- Contextual pre-filtering: moderate. LLM-based analysis of task specs, integration with allow-list resolution.

These are independent and can be tackled in any order. Graph edges are the most immediately useful (static relationships for built-in tools require no infrastructure). Trace-based inference has the most dependencies.
