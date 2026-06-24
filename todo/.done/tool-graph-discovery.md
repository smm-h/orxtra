# Tool graph and discovery for large registries

## Problem

orxtra's tool registry is a flat list. Every agent session receives tools from this flat pool filtered only by its `allow` list. This works at small scale but breaks down as consumer projects grow.

A consumer project currently has 11 integrations with ~15 tools each (~165 tools today, growing). At this scale:

- Agents waste tokens scanning tool names and descriptions to find relevant ones. With 200+ tools, the tool definitions alone consume a significant portion of the context window.
- Flat `allow` lists become maintenance burdens. Every new tool requires updating every agent definition that might need it.
- No semantic relationship between tools. An agent using `deploy_staging` has no way to discover that `check_run` and `git_push` are related without having all three in its allow list already.
- The problem compounds: more integrations means more tools, which means more irrelevant tools per agent, which means more wasted tokens and slower discovery.

The threshold is roughly ~50 tools. Below that, scanning the full list is tolerable. Above it, agents need a discovery mechanism.

## Requirements

1. **Task-relevant surfacing.** An agent working on a "deploy" task should see deploy-relevant tools first, without requiring manual per-task tool lists.
2. **Automatic organization.** The mechanism should derive tool relationships from structure, usage patterns, or metadata -- not require manual tagging of every tool by the consumer.
3. **Incremental discovery.** Agents should be able to start with a small relevant set and pull in additional tools as needed, rather than receiving all tools upfront.
4. **Consumer-agnostic.** The mechanism lives in orxtra's tool module, not in consumer projects. Different consumers with different tool sets benefit from the same discovery infrastructure.
5. **No silent degradation.** If discovery fails or returns no results, that is a hard error, not a silent fallback to "give the agent everything."

## Possible approaches

### Tool graph

Tools have typed edges to related tools. `deploy_staging` has edges to `check_run` (depends-on), `git_push` (follows), `get_deploy_status` (related). When an agent starts a task, it receives a seed set of tools. As it uses tools, the graph surfaces adjacent tools that become available.

- Pros: rich semantic relationships, supports incremental discovery naturally, agents navigate toward what they need
- Cons: graph construction is the hard part -- who defines the edges? Manual edge definition doesn't scale. Automatic inference from co-usage patterns requires historical data. Cold-start problem for new tools.

### Capability tags

Tools tagged with capability labels ("deploy", "git", "analytics", "monitoring"). Agent's task spec includes relevant capabilities. Tool registry filters by matching tags.

- Pros: simple to implement, easy to understand, filtering is fast
- Cons: tag assignment is manual (violates requirement 2 unless tags are inferred). Tag granularity is a design problem -- too coarse and you still get 50 tools, too fine and you need per-tool tags which is just a different flat list. Tags don't capture relationships between tools.

### Hierarchical namespaces

Tools grouped by integration or domain: `linear.*`, `figma.*`, `github.*`, `deploy.*`. Agent selects relevant namespaces. Within a namespace, all tools are available.

- Pros: natural grouping for integration-based tools, easy to select "I need all Figma tools"
- Cons: cross-cutting concerns don't fit (a deploy flow touches github, cloud provider, and monitoring). Namespace assignment is often obvious (integration source) but sometimes ambiguous. Doesn't help within a large namespace.

### Contextual filtering

Based on the task spec's description, pre-checks, post-checks, and available assertions, automatically filter to tools that are relevant to the task's domain. The scheduler analyzes what the task needs and selects tools accordingly.

- Pros: fully automatic, no manual annotation, adapts to task context
- Cons: requires a reliable mapping from task descriptions to tool relevance -- likely needs an LLM call itself, which adds latency and cost. Accuracy is uncertain. The filter decision is opaque.

### LLM-based selection

Agent receives a compact list of tool names + one-line descriptions (not full schemas). It selects the tools it thinks it needs. Only selected tools get full schema expansion.

- Pros: leverages the agent's understanding of its own task, minimal infrastructure, works with any tool set
- Cons: two-phase tool loading adds a round-trip. The agent might miss tools it doesn't know it needs. Selection quality depends on description quality. Still sends all names+descriptions (though much cheaper than full schemas).

## Notes

These approaches are not mutually exclusive. A practical solution might combine hierarchical namespaces (for coarse grouping) with a graph or LLM-based selection (for fine-grained discovery within groups).

The tool module already has the `allow` list mechanism. Whatever discovery mechanism is chosen should compose with `allow` -- the allow list is the hard ceiling, discovery operates within it.

The custom tool injection mechanism (see scheduler-custom-tool-injection.md) is a prerequisite for consumer-provided tools. Discovery must work for both built-in and custom tools.
