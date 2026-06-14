# Knowledge Module Design

Experimental semantic enrichment layer over the flat `lessons` table. Wraps the cognee library to provide semantically queryable knowledge retrieval as an enhancement to plain SQL tag-matching.

## Experimental Status

This module is experimental. The system operates fully without it. It may be removed if cognee does not demonstrate measurable improvement in Overseer decision quality on real workloads.

All design decisions assume this module might not exist. No other module has a hard dependency on it. The lessons table (owned by trace/) carries the full load either way.

**Evaluation criteria**: does semantic graph retrieval produce better context (measured by downstream task success rates and coherence scores) than flat tag-matching on the same lessons?

If the evaluation concludes that cognee does not justify its complexity (token cost per ingestion, nondeterministic retrieval, third-party dependency), this module is removed without affecting any other module.

## Responsibility

Index lessons from the lessons table into a cognee knowledge graph. Retrieve semantically relevant knowledge during context assembly. Manage the graph's lifecycle: ingestion, refinement, staleness handling.

This module does NOT load consumer knowledge files. The Overseer owns that (see `overseer/DESIGN.md`). This module passively indexes what the Overseer writes to the lessons table.

## What This Module Is

- A semantic index over the lessons table, not a replacement for it
- A wrapper around cognee's ECL pipeline and retrieval API
- A passive indexer: reads from the lessons table, writes to the cognee graph

## What This Module Is NOT

- Not the primary knowledge store (the lessons table is)
- Not the consumer knowledge loader (the Overseer is)
- Not the Overseer's decision memory
- Not required for orxt to function

## Architecture

### Storage Backend

Cognee uses the same PostgreSQL instance:
- Relational store: `db_provider = "postgres"`
- Vector store: `vector_db_provider = "pgvector"` (pgvector extension required)
- Graph store: cognee's default embedded graph engine

### Data Flow

```
Overseer loads knowledge files
        |
        v
  trace.write_lesson()  -----> lessons table (primary store)
        |
        v                          |
  (runtime lessons from           v
   completed runs)        knowledge/_ingest.py --> cognee.cognify() --> knowledge graph
                                                                          |
                                                                          v
                                                                knowledge/_retrieve.py
                                                                          |
                                                                          v
                                                             Overseer context assembly
```

### Runtime Failure

If `KnowledgeConfig` is provided and cognee fails at runtime, that is a **hard error**. No silent degradation to flat SQL. Fix cognee or remove `KnowledgeConfig`.

## Ingestion

### From Lessons Table

The module periodically indexes new rows from the lessons table:
- Content-hash tracking prevents re-ingesting unchanged entries
- Consumer knowledge (permanent entries) re-ingested only if hash changes
- Runtime learnings from completed runs indexed post-run

### Ingestion Cost

`cognee.cognify()` uses an LLM for entity extraction. The LLM is configured separately from the Overseer's model -- it should use a cheap, fast model.

## Retrieval

```python
async def retrieve_knowledge(
    query: str,
    tags: list[str] | None = None,
    max_results: int = 10,
) -> list[KnowledgeResult]:
```

Returns empty list if `KnowledgeConfig` was not provided.

## Configuration

```python
@dataclass(frozen=True)
class KnowledgeConfig:
    db_url: str
    knowledge_dir: Path
    cognify_model: str
    cognify_api_key: str
    max_retrieval_results: int
```

Active when `KnowledgeConfig` is provided. When not provided, the module is inert. Presence of config IS the signal.

## Files

| File | Contents |
|---|---|
| `_types.py` | `KnowledgeConfig`, `KnowledgeResult`. Custom DataPoint subclasses. |
| `_ingest.py` | Index lessons table rows into cognee. Content-hash tracking. |
| `_retrieve.py` | `retrieve_knowledge()` -- wraps cognee.search(). |
| `_config.py` | Cognee backend configuration. |
| `_freshness.py` | Content-hash tracking. Memify dispatch. |

## What This Module Does NOT Do

- Does not own the lessons table (that is trace/)
- Does not write lessons (the Overseer writes via trace)
- Does not load consumer knowledge files (the Overseer does)
- Does not replace flat SQL retrieval -- it enriches it
- Does not function when KnowledgeConfig is not provided
