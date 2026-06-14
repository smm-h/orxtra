# Knowledge Module Design

Knowledge ingestion and retrieval via cognee. Wraps the cognee library to provide the Overseer with structured, queryable, evolving domain knowledge.

## Responsibility

Ingest consumer knowledge files and runtime learnings into a knowledge graph. Retrieve relevant knowledge during the Overseer's context assembly. Manage the graph's lifecycle across runs: ingestion, refinement, staleness handling.

This module replaces two pieces that previously lived in the Overseer module:
- Consumer knowledge file loading (`_knowledge.py` -- moved here)
- Cross-run knowledge base persistence and staleness detection (`_learning.py` -- moved here)

## What This Module Is

- The knowledge layer between raw domain files and the Overseer's context
- A wrapper around cognee's ECL pipeline (Extract, Cognify, Load) and retrieval API
- The owner of the knowledge graph's lifecycle (ingestion, refinement, retrieval)

## What This Module Is NOT

- Not the Overseer's decision memory. Decisions, constraints, assumptions, and workflow status remain flat PG tables owned by the trace module. Those are structured, bounded, and well-served by SQL.
- Not a code intelligence system. The knowledge graph stores domain knowledge and learned patterns, not ASTs, symbol tables, or import graphs.
- Not a general-purpose graph database. The graph is consumed exclusively by the Overseer's context assembly.

## Why Cognee

Cognee won empirical benchmarks (kb-bench, 700 queries, 7 frameworks) on retrieval quality with an average score of 89.8, beating mem0 (81.8), fast-graphrag (79.3), and five others. It supports PG+pgvector as its storage backend (no external graph database), aligning with oxtra's PG backbone. Its ECL pipeline handles structured document ingestion -- the exact workload consumer knowledge files and runtime learnings represent.

## Architecture

### Storage Backend

Cognee is configured to use the same PostgreSQL instance as the rest of oxtra:
- **Relational store**: `db_provider = "postgres"` -- same `db_url` as the trace module
- **Vector store**: `vector_db_provider = "pgvector"` -- pgvector extension on the same PG instance
- **Graph store**: cognee's default embedded graph engine (Kuzu) -- no external graph DB

The pgvector extension must be enabled on the oxtra database. This is the only additional PG requirement beyond the trace module's schema.

### Data Flow

```
Consumer knowledge files (.md, .toml)
        |
        v
  knowledge/_ingest.py  --cognee.add()--> cognee.cognify() --> knowledge graph
        ^                                                           |
        |                                                           v
  Runtime learnings                                    knowledge/_retrieve.py
  (notepad entries,                                           |
   step outcomes,                                             v
   failure patterns)                              Overseer context assembly
                                                       (Layer 3)
```

### Integration with Existing Modules

**Overseer** -- `_memory.py` context assembly gains a call to `knowledge/_retrieve.py` for Layer 3 (Overseer-selected lessons). Flat PG queries for decisions, constraints, and assumptions are unchanged.

**Trace** -- the `lessons` table is removed from the trace schema. Knowledge persistence moves to cognee's graph. All other trace tables are unaffected.

**Notepad** -- notepad entries from completed runs can be selectively ingested into the knowledge graph as runtime learnings (via a post-run ingestion step), enabling cross-run knowledge accumulation.

## Ingestion

### Consumer Knowledge Files

At the start of every run, the module loads files from the consumer's `knowledge/` directory:

**Markdown files** (`.md`) -- free-form domain knowledge. Each file is ingested via `cognee.add()` and `cognee.cognify()`, producing graph nodes and edges from the content. Files can contain YAML front matter for metadata:

```markdown
---
tags: [code-quality, determinism]
---
Never use Math.random() or Date.now() in generated code. These break deterministic replay.
```

Front matter `tags` become metadata on the ingested nodes, improving retrieval relevance.

**TOML files** (`.toml`) -- structured constraints. These are ingested into the knowledge graph as typed nodes with `constraint` type and `mechanical`/`advisory` tier:

```toml
[[constraints]]
text = "All generated code must pass lint and type checks before commit"
tier = "mechanical"

[[constraints]]
text = "Prefer composition over inheritance in generated components"
tier = "advisory"
```

Consumer knowledge is marked as permanent in the graph -- it does not decay via memify and is not subject to staleness detection.

### Runtime Learnings

After a run completes, the module can ingest runtime observations into the knowledge graph:

- **Notepad entries** tagged as `learning` from the completed run
- **Failure patterns** -- recurring error categories and their resolutions
- **Constraint outcomes** -- which constraints were useful vs. which were violated and relaxed

Runtime learnings are transient in the graph -- subject to memify refinement (staleness pruning, frequency reweighting) and staleness detection via git (if a source file path is associated).

### Ingestion Cost

Every `cognee.cognify()` call uses the LLM for entity/relationship extraction. This cost is:
- **Per-run for consumer knowledge**: bounded by the size of the `knowledge/` directory. Consumer files are re-ingested only if their content hash changes since the last ingestion.
- **Post-run for runtime learnings**: bounded by the number of notepad entries and failure records. Selective -- only `learning`-type entries, not all notepad content.

The LLM used for cognify is configured separately from the Overseer's model -- it should use a cheap, fast model (e.g., the `quick` category) since extraction is a batch operation, not a judgment call.

## Retrieval

### Query API

```python
async def retrieve_knowledge(
    query: str,
    tags: list[str] | None = None,
    max_results: int = 10,
) -> list[KnowledgeResult]:
    """
    Retrieve relevant knowledge from the graph for context assembly.

    Uses cognee's GRAPH_COMPLETION search type for multi-hop graph traversal
    combined with semantic similarity.
    """
    ...
```

```python
@dataclass(frozen=True)
class KnowledgeResult:
    text: str              # The knowledge content, serialized for prompt injection
    source: str            # Where this knowledge came from (file path, run ID, step name)
    permanent: bool        # True for consumer knowledge, false for runtime learnings
    relevance_score: float # Cognee's relevance ranking
```

### Integration with Context Assembly

The Overseer's context assembly (scheduler, Layer 3) calls `retrieve_knowledge()` with a query derived from the current step's task and type. The Overseer's `context_decision` protocol then refines the results: selecting which knowledge to include, reordering for relevance, or discarding irrelevant items.

The context assembly code constructs the query from:
- The step's task prompt (what the agent will do)
- The step's agent name and category (what kind of work)
- Active constraints (what rules apply)

Results are formatted as a "Relevant Knowledge" section in the agent's context, alongside the existing notepad injection and constraint listing.

### Retrieval Modes

Cognee supports 14 retrieval modes. The module uses:
- **`GRAPH_COMPLETION`** as the default: multi-hop graph traversal + semantic similarity, producing synthesized answers from the graph
- **`CHUNKS`** as a fallback for simple keyword lookups: returns raw matched text without graph traversal

The retrieval mode is not configurable per query -- it uses GRAPH_COMPLETION for all context assembly queries. If retrieval quality proves insufficient for specific decision types, this can be revisited.

## Custom Graph Model

To constrain cognee's entity extraction and reduce hallucinated edges, the module defines a custom graph model (Pydantic DataPoint subclasses) for the kinds of nodes oxtra cares about:

```python
class DomainConcept(DataPoint):
    """A concept from the consumer's domain."""
    name: str
    description: str
    tags: list[str] = []

class Convention(DataPoint):
    """A coding or architectural convention."""
    text: str
    tier: str  # "mechanical" or "advisory"

class FailurePattern(DataPoint):
    """A recurring failure mode and its resolution."""
    error_category: str
    pattern: str
    resolution: str
    source_step: str | None = None

class LearnedFact(DataPoint):
    """A fact learned during a run."""
    text: str
    source_run_id: str | None = None
    source_step: str | None = None
    permanent: bool = False
```

This schema constrains what cognee extracts from ingested content. The LLM produces nodes of these types and edges between them, not arbitrary graph structures. The schema is extensible -- consumers can provide additional DataPoint subclasses via a registration API.

## Staleness and Freshness

Two mechanisms:

**Content-hash tracking**: consumer knowledge files are hashed at ingestion. On subsequent runs, only files whose hash changed are re-ingested. Unchanged files skip the cognify step entirely (no LLM cost).

**Memify refinement**: after each run's learnings are ingested, `cognee.memify()` runs to prune stale nodes, strengthen frequently-accessed connections, and reweight edges. This is cognee's built-in memory refinement -- it makes the graph self-improving over time.

**Git-based staleness** (from the prior design): if a `LearnedFact` node carries a `source_file` path, the module checks via git whether that file has changed since the fact was learned. Changed-source facts are flagged as potentially stale and deprioritized in retrieval.

## Configuration

The module requires explicit configuration -- no implicit defaults:

```python
@dataclass(frozen=True)
class KnowledgeConfig:
    db_url: str                    # Same PG connection as trace
    knowledge_dir: Path            # Consumer's knowledge/ directory
    cognify_model: str             # Model for cognee's entity extraction (e.g., "quick" category)
    cognify_api_key: str           # API key for the extraction LLM
    max_retrieval_results: int     # Default max results for retrieve_knowledge()
```

All fields are required. Missing values are hard errors.

## Files

| File | Contents |
|---|---|
| `_types.py` | `KnowledgeConfig`, `KnowledgeResult` frozen dataclasses / pydantic models. Custom DataPoint subclasses (`DomainConcept`, `Convention`, `FailurePattern`, `LearnedFact`). |
| `_ingest.py` | `ingest_consumer_knowledge(config, knowledge_dir)` -- loads .md and .toml files, content-hash checks, calls cognee.add() + cognee.cognify(). `ingest_runtime_learnings(config, run_id, trace_reader)` -- selectively ingests notepad entries and failure patterns from a completed run. |
| `_retrieve.py` | `retrieve_knowledge(query, tags, max_results)` -- wraps cognee.search() with GRAPH_COMPLETION mode, returns typed KnowledgeResult list. |
| `_config.py` | Cognee backend configuration: sets db_provider, vector_db_provider, graph model registration, LLM provider for cognify. |
| `_freshness.py` | Content-hash tracking for consumer files (skip re-ingestion of unchanged files). Git-based staleness detection for learned facts with source file paths. Memify dispatch after runtime learning ingestion. |

## What This Module Does NOT Do

- Does not store or query decisions, constraints, assumptions, or workflow status (those stay in trace's flat PG tables)
- Does not define what knowledge the consumer provides (that's the consumer's `knowledge/` directory)
- Does not execute agents or make judgment calls
- Does not bypass cognee's retrieval for direct graph queries -- all access goes through cognee's search API
- Does not build code intelligence (no AST parsing, no symbol indexing, no import graphs)
- Does not manage cognee's internal schema migrations or version upgrades (that's cognee's responsibility as a dependency)
