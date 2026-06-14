# Secrets Module Design

Secret registry, substitution, and scrubbing. Foundation layer -- zero intra-workspace dependencies.

## Responsibility

Ensure that registered secret values never reach the LLM or the database. Three mechanisms:

1. **Registry**: consumers register secrets as `{"NAME": "value"}` at run construction. The registry holds them in memory.
2. **Substitution**: before a tool's `execute()` is called, `{{secret:NAME}}` placeholders in tool arguments are replaced with real values. The LLM only ever sees the placeholder.
3. **Scrubbing**: after `execute()` returns, any registered secret value found in the result is replaced with its placeholder. This catches cases where a tool accidentally surfaces a secret (e.g., an error message containing an API key).

The same scrubbing applies to trace persistence -- the trace module calls the scrubber before writing tool results, transcripts, and events.

## Guarantee

Registered secret values provably never appear in:
- The LLM's context (substitution happens after the LLM emits tool calls, scrubbing happens before results enter the LLM's context)
- The database (scrubbing happens before trace persistence)
- Log output (scrubbing is applied to all externally visible strings)

## Secret Registry

```python
class SecretRegistry:
    def __init__(self, secrets: dict[str, str]): ...
    def substitute(self, text: str) -> str:
        """Replace {{secret:NAME}} placeholders with real values."""
    def scrub(self, text: str) -> str:
        """Replace real secret values with {{secret:NAME}} placeholders."""
```

The registry is immutable after construction. Secrets cannot be added, removed, or modified during a run.

Substitution is exact-match on `{{secret:NAME}}` patterns. Scrubbing is exact-match on the registered values (longest-first to handle overlapping values).

## Files

| File | Contents |
|---|---|
| `_registry.py` | `SecretRegistry` class. Registration, substitution, scrubbing. |

## What This Module Does NOT Do

- Does not store secrets on disk or in the database
- Does not manage secret rotation or expiry
- Does not integrate with external secret managers (consumers pass secrets directly)
- Does not encrypt secrets in memory
