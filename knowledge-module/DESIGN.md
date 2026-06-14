# Knowledge Module Design

Semantic enrichment layer over the flat `lessons` table. Wraps the cognee library to provide the Overseer with graph-structured, semantically queryable knowledge retrieval as an enhancement to plain SQL tag-matching.

## Experimental Status

Cognee integration is experimental. It must be thoroughly evaluated before it becomes load-bearing:

- The flat `lessons` table (owned by trace/) is the **primary store** and the **source of truth**. Every learned fact is written there first — transactional, deterministic, queryable with plain SQL, no external dependency.
- This module indexes lessons into cognee's knowledge graph as a **semantic enrichment layer**. If cognee is absent, misconfigured, or underperforming, context assembly falls back to flat SQL queries against the lessons table with tag-based filtering. This is not a "try A, if that fails try B" fallback — it's a primary store + optional index architecture, like a database + search engine.
- Before cognee becomes the default retrieval path, it must demonstrate measurable improvement in Overseer decision quality on real workloads. The evaluation criteria: does semantic graph retrieval produce better context (measured by downstream step success rates and coherence scores) than flat tag-matching on the same lessons?

If the evaluation concludes that cognee does not justify its complexity (token cost per ingestion, nondeterministic retrieval, third-party dependency), this module can be removed without affecting any other module. The lessons table carries the full load either way.

## Responsibility

Ingest lessons and consumer knowledge files into a cognee knowledge graph. Retrieve semantically relevant knowledge during the Overseer's context assembly (Layer 3). Manage the graph's lifecycle across runs: ingestion, refinement, staleness handling.

This module owns:
- Consumer knowledge file loading (`.md` and `.toml` from the `knowledge/` directory) — writes to both the lessons table (via trace) AND cognee's graph
- Runtime learning ingestion — indexes notepad entries and failure patterns from the lessons table into cognee after run completion
- Semantic retrieval — `retrieve_knowledge()` used by the Overseer's context assembly alongside flat SQL queries

This module does NOT own:
- The lessons table itself (that's trace/)
- Writing lessons (the Overseer writes via `trace.write_lesson()`; this module reads and indexes)
- Decisions, constraints, assumptions, workflow status (flat PG tables, never in the graph)

## What This Module Is

- A semantic index over the lessons table, not a replacement for it
- A wrapper around cognee's ECL pipeline (Extract, Cognify, Load) and retrieval API
- An experimental enrichment layer that must prove its value

## What This Module Is NOT

- Not the primary knowledge store. The lessons table is.
- Not the Overseer's decision memory. Decisions, constraints, assumptions remain flat PG tables.
- Not a code intelligence system. No ASTs, symbol tables, or import graphs.
- Not required for orxt to function. Context assembly works with flat SQL alone.

## Why Cognee

Cognee won empirical benchmarks (kb-bench, 700 queries, 7 frameworks) on retrieval quality with an average score of 89.8, beating mem0 (81.8), fast-graphrag (79.3), and five others. It supports PG+pgvector as its storage backend (no external graph database), aligning with orxt's PG backbone. Its ECL pipeline handles structured document ingestion -- the exact workload consumer knowledge files represent.

These results are on a static document retrieval benchmark, not on evolving agent memory. Whether the advantage holds for the Overseer's runtime workload is an open question -- the reason this is experimental.

## Architecture

### Storage Backend

Cognee is configured to use the same PostgreSQL instance as the rest of orxt:
- **Relational store**: `db_provider = "postgres"` -- same `db_url` as the trace module
- **Vector store**: `vector_db_provider = "pgvector"` -- pgvector extension on the same PG instance
- **Graph store**: cognee's default embedded graph engine (Kuzu) -- no external graph DB

The pgvector extension must be enabled on the orxt database. This is the only additional PG requirement beyond the trace module's schema.

### Data Flow

```
Consumer knowledge files (.md, .toml)
        |
        v
  trace.write_lesson()  -----> lessons table (primary store, flat SQL)
        |
        v
  knowledge/_ingest.py  --cognee.add()--> cognee.cognify() --> knowledge graph (semantic index)
        ^                                                           |
        |                                                           v
  Runtime learnings                                    knowledge/_retrieve.py
  (lessons table rows                                         |
   from completed runs)                                       v
                                                  Overseer context assembly
                                                       (Layer 3, alongside
                                                        flat SQL queries)
```

### Integration with Existing Modules

**Overseer** — `_memory.py` context assembly queries the lessons table with flat SQL (always available) AND calls `knowledge/_retrieve.py` for semantic graph retrieval (when the knowledge module is configured). Both results are provided to the `context_decision` protocol.

**Trace** — owns the `lessons` table. This module reads from it for ingestion into cognee. Never writes to it — the Overseer writes lessons via `trace.write_lesson()`.

**Notepad** — notepad entries from completed runs can be selectively ingested into the knowledge graph as runtime learnings.

## Ingestion

### Consumer Knowledge Files

At the start of every run, the module loads files from the consumer's `knowledge/` directory:

**Markdown files** (`.md`) — free-form domain knowledge. Each file is:
1. Written to the lessons table as a permanent entry (via `trace.write_lesson()`)
2. Ingested into cognee via `cognee.add()` + `cognee.cognify()`, producing graph nodes and edges

Files can contain YAML front matter for metadata:

```markdown
---
tags: [code-quality, determinism]
---
Never use Math.random() or Date.now() in generated code. These break deterministic replay.
```

**TOML files** (`.toml`) — structured constraints. Written to the constraints table (via `trace.write_constraint()`) AND ingested into cognee as typed constraint nodes.

Consumer knowledge is marked as permanent in both the lessons table AND the cognee graph — it does not decay via memify and is not subject to staleness detection.

### Runtime Learnings

After a run completes, the module can ingest lessons table rows from that run into cognee's graph:

- Notepad entries tagged as `learning`
- Failure patterns — recurring error categories and their resolutions
- Constraint outcomes — which constraints were useful vs. violated and relaxed

Runtime learnings are transient in cognee's graph — subject to memify refinement (staleness pruning, frequency reweighting). In the lessons table, they follow the existing staleness/expiry rules.

### Ingestion Cost

Every `cognee.cognify()` call uses the LLM for entity/relationship extraction. This cost is:
- **Per-run for consumer knowledge**: bounded by the size of the `knowledge/` directory. Consumer files are re-ingested only if their content hash changes since the last ingestion.
- **Post-run for runtime learnings**: bounded by the number of new lessons table rows.

The LLM used for cognify is configured separately from the Overseer's model — it should use a cheap, fast model since extraction is a batch operation, not a judgment call.

## Retrieval

### Query API

```python
async def retrieve_knowledge(
    query: str,
    tags: list[str] | None = None,
    max_results: int = 10,
) -> list[KnowledgeResult]:
    """
    Retrieve relevant knowledge from the cognee graph.

    Uses cognee's GRAPH_COMPLETION search type for multi-hop graph traversal
    combined with semantic similarity. Returns empty list if KnowledgeConfig
    was not provided.
    """
    ...
```

```python
@dataclass(frozen=True)
class KnowledgeResult:
    text: str              # The knowledge content
    source: str            # Where this came from (file path, run ID, step name)
    permanent: bool        # True for consumer knowledge
    relevance_score: float # Cognee's relevance ranking
```

### Integration with Context Assembly

The Overseer's context assembly (Layer 3) has two knowledge sources:

1. **Flat SQL** (always available): `SELECT * FROM lessons WHERE (run_id = $1 OR permanent = true) AND relevance_tags && $2` — deterministic, bounded, zero external dependency
2. **Cognee retrieval** (when configured): `retrieve_knowledge(query)` — semantic, graph-traversal, nondeterministic

The `context_decision` protocol receives results from both and refines them. If `KnowledgeConfig` was not provided, only flat SQL results are available — the system works fully without cognee.

## Custom Graph Model

To constrain cognee's entity extraction:

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

The schema is extensible — consumers can provide additional DataPoint subclasses via a registration API.

## Staleness and Freshness

**Content-hash tracking**: consumer knowledge files are hashed at ingestion. On subsequent runs, only changed files are re-ingested.

**Memify refinement**: after each run's learnings are ingested, `cognee.memify()` runs to prune stale nodes and strengthen frequent connections.

**Git-based staleness**: if a lesson carries a `source_file` path, the lessons table's staleness detection (in the Overseer module) checks via git whether that file has changed. This applies to the flat table regardless of cognee.

## Configuration

```python
@dataclass(frozen=True)
class KnowledgeConfig:
    db_url: str                    # Same PG connection as trace
    knowledge_dir: Path            # Consumer's knowledge/ directory
    cognify_model: str             # Model for cognee's entity extraction
    cognify_api_key: str           # API key for the extraction LLM
    max_retrieval_results: int     # Default max results for retrieve_knowledge()
```

Cognee is active when a `KnowledgeConfig` is provided to the run. When it is not provided (`None`), the module is inert — no cognee calls, no ingestion, no retrieval. Context assembly uses flat SQL only. Presence of the config object IS the signal, not a boolean flag (anti-pattern #9).

## Files

| File | Contents |
|---|---|
| `_types.py` | `KnowledgeConfig`, `KnowledgeResult` pydantic models. Custom DataPoint subclasses (`DomainConcept`, `Convention`, `FailurePattern`, `LearnedFact`). |
| `_ingest.py` | `ingest_consumer_knowledge(config, knowledge_dir, trace_writer)` — loads .md and .toml files, writes to lessons table via trace, ingests into cognee. `ingest_runtime_learnings(config, run_id, trace_reader)` — indexes lessons table rows from a completed run into cognee. |
| `_retrieve.py` | `retrieve_knowledge(query, tags, max_results)` — wraps cognee.search(), returns KnowledgeResult list. Returns empty list if KnowledgeConfig was not provided. |
| `_config.py` | Cognee backend configuration: db_provider, vector_db_provider, graph model registration, LLM provider for cognify. |
| `_freshness.py` | Content-hash tracking for consumer files. Memify dispatch after runtime learning ingestion. |

## What This Module Does NOT Do

- Does not own the lessons table (that's trace/)
- Does not write lessons (the Overseer writes via trace.write_lesson())
- Does not store or query decisions, constraints, assumptions, or workflow status
- Does not replace flat SQL retrieval — it enriches it
- Does not function when KnowledgeConfig is not provided — flat SQL carries the full load
- Does not build code intelligence
