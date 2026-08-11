# Token Usage

The single owner of token accounting. Every source — a LangChain callback, an OpenAI wrapper, a Bedrock wrapper — converts its own response shape into **one normalized dict**, then hands it to **one writer**. Nothing else touches a `gen_ai.usage.*` attribute.

```
provider / framework response  ->  adapter  ->  normalized usage dict  ->  set_usage_attributes(span, ...)
                                                                       ->  record_model_operation(..., usage=...)
```

Without that funnel, each call site maps `prompt_tokens` its own way, an SDK upgrade is a forty-site migration, and the cost dashboard quietly disagrees with the trace.

Read `attributes.md` first for the constants module this imports from.

---

## The normalized shape

This skill's internal contract. It matches LangChain's `usage_metadata` layout because that is already a reasonable cross-provider normalization — but it is **ours**, and a service with no LangChain in it uses the same shape.

```json
{
  "input_tokens": 1000,
  "output_tokens": 200,
  "total_tokens": 1200,
  "input_token_details": {"cache_read": 700, "cache_creation": 100, "audio": 0},
  "output_token_details": {"reasoning": 80, "audio": 0}
}
```

Every field is optional. Providers differ in what they report, and a field that is absent today appears after someone enables prompt caching.

---

## The writer

```python
# observability/genai_usage.py
import json
from typing import Any

from opentelemetry.trace import Span

from observability.genai_attributes import (
    APP_INPUT_TOKEN_DETAILS,
    APP_OUTPUT_TOKEN_DETAILS,
    GENAI_CACHE_CREATION_INPUT_TOKENS,
    GENAI_CACHE_READ_INPUT_TOKENS,
    GENAI_INPUT_TOKENS,
    GENAI_OUTPUT_TOKENS,
    GENAI_REASONING_OUTPUT_TOKENS,
)


def normalize_usage(usage: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten a normalized usage dict into the fields the writer needs.

    Accepts the shape produced by any adapter below. Every field is optional,
    so the caller must tolerate None.
    """
    usage = usage or {}
    input_details = usage.get("input_token_details") or {}
    output_details = usage.get("output_token_details") or {}

    return {
        "input_tokens": usage.get("input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "total_tokens": usage.get("total_tokens"),
        "input_token_details": input_details or None,
        "output_token_details": output_details or None,
        "cache_read_tokens": input_details.get("cache_read", 0),
        "cache_creation_tokens": input_details.get("cache_creation", 0),
        "reasoning_tokens": output_details.get("reasoning", 0),
    }


def set_usage_attributes(span: Span, usage: dict[str, Any] | None) -> None:
    """Write normalized token usage onto a model span."""
    normalized = normalize_usage(usage)

    if normalized["input_tokens"] is not None:
        span.set_attribute(GENAI_INPUT_TOKENS, normalized["input_tokens"])
    if normalized["output_tokens"] is not None:
        span.set_attribute(GENAI_OUTPUT_TOKENS, normalized["output_tokens"])

    # Default to 0 rather than omitting: "no cache hit" and "provider does not
    # report caching" look identical on a dashboard if the attribute is absent.
    span.set_attribute(
        GENAI_CACHE_READ_INPUT_TOKENS, normalized["cache_read_tokens"]
    )
    span.set_attribute(
        GENAI_CACHE_CREATION_INPUT_TOKENS, normalized["cache_creation_tokens"]
    )
    span.set_attribute(
        GENAI_REASONING_OUTPUT_TOKENS, normalized["reasoning_tokens"]
    )

    # Everything else the provider reported — audio, accepted/rejected
    # prediction tokens, provider-specific buckets — survives here verbatim.
    # "" when unavailable, so the attribute's presence is stable.
    input_details = normalized["input_token_details"]
    output_details = normalized["output_token_details"]
    span.set_attribute(
        APP_INPUT_TOKEN_DETAILS, json.dumps(input_details) if input_details else ""
    )
    span.set_attribute(
        APP_OUTPUT_TOKEN_DETAILS, json.dumps(output_details) if output_details else ""
    )
```

The same normalized dict goes to `record_model_operation(usage=...)` so the span and the token histogram can never disagree — see `../../metrics/genai.md`.

### The mapping rule

```
scalar counts with a standard attribute  ->  the gen_ai.usage.* attribute
everything else in the detail maps       ->  one serialized app.* attribute per direction
```

Three decisions behind it:

- **`reasoning` is promoted to a standard attribute.** `gen_ai.usage.reasoning.output_tokens` exists, so it should not be buried inside a JSON blob where no backend can aggregate it.
- **`total_tokens` is dropped, not stored.** It is `input + output`, and a derived scalar is one more field every dashboard has to reconcile against the two it was derived from. Note that the writer discards it outright — it is a sibling of the detail maps, not inside them, so it does not survive in `app.gen_ai.usage.*_token_details` either. Recompute it at query time. If a provider's total genuinely differs from `input + output` (some count differently for billing), that is a real fact and needs its own `app.*` attribute rather than being silently lost.
- **The detail maps go under `app.*`, not `gen_ai.*`.** `gen_ai.usage.input_token_details` is not a real convention. Inventing a key inside a standard namespace collides with whatever OTel eventually ships there.

Capture every figure the source exposes. Token counts drive cost, prompt-growth detection, and cache-effectiveness analysis, and none of them can be reconstructed after the fact. If a provider reports billable and raw tokens separately, use billable tokens for the standard attributes.

When the provider reports no details, the writer still emits a stable set:

```json
{
  "input_tokens": 1000, "output_tokens": 200, "total_tokens": 1200,
  "input_token_details": null, "output_token_details": null,
  "cache_read_tokens": 0, "cache_creation_tokens": 0, "reasoning_tokens": 0
}
```

---

## Adapters

One per source. This file is where the mapping is *specified*; the code goes next to its only caller — the LangChain adapter in the callbacks module, a provider adapter beside its client wrapper. What must not be duplicated is the mapping itself: every adapter produces exactly the shape above, and nothing but `set_usage_attributes` writes it to a span.

### LangChain `LLMResult`

Used by the model callback (`langchain/model_callback.md`). `on_llm_end` receives an `LLMResult`, not a message; the normalized usage lives on the generation's message.

```python
def extract_usage_metadata(response: Any) -> dict[str, Any] | None:
    """Pull normalized usage out of an LLMResult."""
    generations = getattr(response, "generations", None) or []
    for generation_list in generations:
        for generation in generation_list:
            message = getattr(generation, "message", None)
            usage = getattr(message, "usage_metadata", None)
            if usage:
                return dict(usage)

    # Older integrations and non-chat models only populate llm_output.
    llm_output = getattr(response, "llm_output", None) or {}
    token_usage = llm_output.get("token_usage")
    if token_usage:
        return {
            "input_tokens": token_usage.get("prompt_tokens"),
            "output_tokens": token_usage.get("completion_tokens"),
            "total_tokens": token_usage.get("total_tokens"),
        }
    return None
```

`usage_metadata` is already in the normalized shape, so this adapter is mostly a lookup. The `llm_output` fallback is not — it carries totals only, and produces no detail maps.

### OpenAI `chat.completions`

Used by the direct-SDK wrapper (`provider_sdk.md`).

```python
def extract_usage(response) -> dict | None:
    """OpenAI chat.completions -> normalized usage shape."""
    usage = getattr(response, "usage", None)
    if usage is None:
        return None

    prompt_details = getattr(usage, "prompt_tokens_details", None)
    completion_details = getattr(usage, "completion_tokens_details", None)

    input_details = {}
    if prompt_details is not None:
        if getattr(prompt_details, "cached_tokens", None) is not None:
            input_details["cache_read"] = prompt_details.cached_tokens
        if getattr(prompt_details, "audio_tokens", None) is not None:
            input_details["audio"] = prompt_details.audio_tokens

    output_details = {}
    if completion_details is not None:
        if getattr(completion_details, "reasoning_tokens", None) is not None:
            output_details["reasoning"] = completion_details.reasoning_tokens
        if getattr(completion_details, "audio_tokens", None) is not None:
            output_details["audio"] = completion_details.audio_tokens

    return {
        "input_tokens": usage.prompt_tokens,
        "output_tokens": usage.completion_tokens,
        "total_tokens": usage.total_tokens,
        "input_token_details": input_details or None,
        "output_token_details": output_details or None,
    }
```

`getattr` with defaults throughout, deliberately. Instrumentation must not be the thing that crashes a request because a provider stopped populating a field.

### Where each provider reports the same facts

Write the equivalent adapter from this table:

| Provider | Input / output totals | Cache read | Cache creation | Reasoning |
| --- | --- | --- | --- | --- |
| OpenAI | `usage.prompt_tokens` / `completion_tokens` | `prompt_tokens_details.cached_tokens` | — | `completion_tokens_details.reasoning_tokens` |
| Anthropic | `usage.input_tokens` / `output_tokens` | `usage.cache_read_input_tokens` | `usage.cache_creation_input_tokens` | reported per model, check the response |
| Bedrock | `usage.inputTokens` / `outputTokens` | model-dependent, in the model response body | model-dependent | model-dependent |
| Azure OpenAI | same as OpenAI | same as OpenAI | — | same as OpenAI |
| Google GenAI / Vertex | `usage_metadata.prompt_token_count` / `candidates_token_count` | `cached_content_token_count` | — | `thoughts_token_count` where exposed |

Verify against the SDK version actually pinned in the project — these field names move.

---

## Streaming: usage is opt-in at the request

Most providers omit token counts from a streamed response unless asked, and the omission is **silent**. The spans look correct; usage is simply zero everywhere.

```python
# Direct SDK: the counts arrive on a final chunk whose `choices` list is empty.
client.chat.completions.create(..., stream=True, stream_options={"include_usage": True})
```

```python
# LangChain: without stream_usage, on_llm_end receives no usage_metadata.
init_chat_model("openai:gpt-5", streaming=True, stream_usage=True)
```

The equivalent for other providers differs; check the integration. **If `gen_ai.usage.input_tokens` is missing on streamed spans but present on non-streamed ones, this is the cause** — not the adapter, and not the callback.

---

## Verify

- `gen_ai.usage.input_tokens` and `gen_ai.usage.output_tokens` are non-zero on a real call.
- `gen_ai.usage.cache_read.input_tokens` and `gen_ai.usage.cache_creation.input_tokens` are present, even when `0`.
- `app.gen_ai.usage.input_token_details` / `output_token_details` are populated when the provider reports details, and `""` when it does not.
- Streamed and non-streamed calls to the same model both carry usage.
- The token histogram in `../../metrics/genai.md` and the span attributes come from the same normalized dict.

Do not discard details because they look empty this time — a model that reports `cache_read: 0` today reports a real number after prompt caching is enabled.
