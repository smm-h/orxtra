# Anthropic streaming parser does not capture input_tokens

## Problem

The Anthropic provider's `parse_stream` method in `transport/src/orxtra/transport/providers/_anthropic.py` does not extract `input_tokens` from the streaming response. This causes all consumers to report 0 input tokens.

## Root cause

In Anthropic's streaming SSE protocol, token usage is split across two events:

- `message_start`: Contains the initial `message` object with `usage.input_tokens` (and `usage.cache_read_input_tokens`, `usage.cache_creation_input_tokens`)
- `message_delta`: Contains only `usage.output_tokens` (the final output count)

The current parser (line 152) lists `message_start` in its skip set alongside `ping`:

```python
elif event_type not in ("message_start", "ping"):
    yield UnknownEvent(raw=data)
```

It then tries to extract `input_tokens` from `message_delta` (lines 137-149), but that field is never present in `message_delta` per the Anthropic API spec. The `.get("input_tokens", 0)` always returns 0.

## Impact

- All input token counts are reported as 0 for streaming Anthropic sessions
- Cost calculations that depend on input tokens are broken (underreporting by 100%)
- The `Usage.cache_read_tokens` and `Usage.cache_write_tokens` are also never populated in streaming mode (they come from `message_start.message.usage`)

## Fix

Handle the `message_start` event to extract input tokens:

```python
elif event_type == "message_start":
    msg_usage = data.get("message", {}).get("usage", {})
    if msg_usage:
        yield StreamUsage(
            usage=Usage(
                input_tokens=msg_usage.get("input_tokens", 0),
                output_tokens=msg_usage.get("output_tokens", 0),
                cache_read_tokens=msg_usage.get("cache_read_input_tokens", 0),
                cache_write_tokens=msg_usage.get("cache_creation_input_tokens", 0),
            ),
        )
```

Then keep the existing `message_delta` handler for `output_tokens` (it reports the final output count which may differ from what `message_start` reports at 0).

The `_accumulate_usage` in `_transport.py` already sums across multiple `StreamUsage` events correctly, so emitting two (one from message_start, one from message_delta) will produce the correct totals.

## Secondary issue: OpenAI provider

The OpenAI provider's `parse_stream` also never emits `StreamUsage`. OpenAI's streaming API requires `stream_options: {"include_usage": true}` to get usage in the final chunk. This should be added to `build_request` and parsed in `parse_stream`.

## Affected files

- `transport/src/orxtra/transport/providers/_anthropic.py` (primary fix)
- `transport/src/orxtra/transport/providers/_openai.py` (secondary)
- `transport/tests/test_streaming.py` (needs test for usage extraction)
