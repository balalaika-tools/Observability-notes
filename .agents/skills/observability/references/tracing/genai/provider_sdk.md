# Direct Provider SDK Instrumentation

For services that call OpenAI, Anthropic, Bedrock, Azure OpenAI, or Google GenAI/Vertex directly, without an agent framework. This is usually the simpler case: standalone model calls rather than an agent loop.

Three neighbours own what this file uses: `attributes.md` (the constants module), `token_usage.md` (`set_usage_attributes` and the per-provider adapters), and `content_capture.md` (the `CAPTURE_AI_CONTENT` switch). Read `attributes.md` first.

## Contents

- [Provider wrapper](#one-wrapper-per-provider-not-a-span-at-every-call-site)
- [Usage adapters](#usage)
- [Streaming](#streaming)
- [Retries](#application-level-retries)
- [Embeddings and retrieval](#other-operations)
- [Checklist](#checklist)

The provider wrappers are **partial integration templates** because the SDK
client and response adapter are service dependencies. Before copying one, add
the exact adapter from `token_usage.md`; the streaming fragment additionally
requires local `extract_stream_usage()` and `extract_text_delta()` helpers for
the locked provider SDK. Do not treat an undefined helper as pseudocode that is
safe to deploy.

---

## One wrapper per provider, not a span at every call site

Put the span, the attribute mapping, and the provider-specific response parsing in one function. Everything else in the service calls that function.

Why: response shapes differ per provider and change between SDK versions. If forty call sites each read `response.usage.prompt_tokens`, an SDK upgrade is a forty-site migration and the attributes drift apart in the meantime.

```python
# llm/openai_client.py
import time
from collections.abc import Sequence
from typing import Any

from opentelemetry import trace
from opentelemetry.trace import SpanKind, Status, StatusCode

from core.config import get_settings
from observability.genai_attributes import (
    ERROR_TYPE,
    GENAI_FINISH_REASONS,
    GENAI_INPUT_MESSAGES,
    GENAI_OPERATION_NAME,
    GENAI_OUTPUT_MESSAGES,
    GENAI_OUTPUT_TYPE,
    GENAI_PROVIDER_NAME,
    GENAI_REQUEST_MAX_TOKENS,
    GENAI_REQUEST_MODEL,
    GENAI_REQUEST_STREAM,
    GENAI_REQUEST_TEMPERATURE,
    GENAI_RESPONSE_ID,
    GENAI_RESPONSE_MODEL,
    GENAI_TIME_TO_FIRST_CHUNK,
)
from observability.genai_content import serialize_messages, serialize_text_output
from observability.genai_metrics import (
    record_model_operation,
    record_time_to_first_chunk,
)
from observability.genai_usage import set_usage_attributes

tracer = trace.get_tracer(__name__)
settings = get_settings()


def complete_chat(
    client: Any,
    messages: Sequence[dict[str, Any]],
    *,
    model: str,
    temperature: float = 0.2,
    max_tokens: int = 800,
) -> str:
    with tracer.start_as_current_span(
        # Low-cardinality: operation plus model, never the prompt.
        f"chat {model}",
        kind=SpanKind.CLIENT,
        record_exception=False,
        # Set at creation time so a sampler can act on them.
        attributes={
            GENAI_OPERATION_NAME: "chat",
            GENAI_PROVIDER_NAME: "openai",
            GENAI_REQUEST_MODEL: model,
            GENAI_REQUEST_STREAM: False,
            GENAI_OUTPUT_TYPE: "text",
            GENAI_REQUEST_TEMPERATURE: temperature,
            GENAI_REQUEST_MAX_TOKENS: max_tokens,
        },
    ) as span:
        started = time.perf_counter()
        error_type: str | None = None
        usage: dict | None = None
        response_model: str | None = None

        if settings.capture_ai_content:
            span.set_attribute(GENAI_INPUT_MESSAGES, serialize_messages(messages))

        try:
            response = client.chat.completions.create(
                model=model,
                messages=list(messages),
                temperature=temperature,
                max_tokens=max_tokens,
            )
            # Extracted inside the try so the finally below can record token
            # metrics as well as duration. On the error path it stays None and
            # only the duration observation is recorded.
            usage = extract_usage(response)
            response_model = getattr(response, "model", None)
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        finally:
            # Duration must be recorded on both paths, or the error rate is
            # computed against a denominator that excludes failures.
            record_model_operation(
                duration_s=time.perf_counter() - started,
                operation="chat",
                provider="openai",
                request_model=model,
                response_model=response_model,
                usage=usage,
                error_type=error_type,
            )

        choice = response.choices[0]
        answer = choice.message.content or ""

        if response_model:
            span.set_attribute(GENAI_RESPONSE_MODEL, response_model)
        span.set_attribute(GENAI_RESPONSE_ID, response.id)
        span.set_attribute(GENAI_FINISH_REASONS, [choice.finish_reason])

        # Same dict the metric got, so span and histogram cannot disagree.
        set_usage_attributes(span, usage)

        if settings.capture_ai_content:
            span.set_attribute(
                GENAI_OUTPUT_MESSAGES,
                serialize_text_output(answer, choice.finish_reason),
            )

        return answer
```

Span kind is `CLIENT` for a remote provider and `INTERNAL` for an in-process model.

The span covers **one logical operation**. If the SDK retries internally, the span covers all of it — that is what the caller experienced. Retries the *application* performs are separate spans; see below.

---

## Usage

`extract_usage()` above is intentionally not defined in this fragment. It is the provider adapter that converts this SDK's response shape into the normalized usage dict, which `set_usage_attributes()` then writes. Copy the complete adapter for the locked SDK from **`token_usage.md`**, keep it next to this wrapper in code, and add an import/compile smoke test so a renamed SDK field fails in CI rather than production.

For Bedrock specifically: read usage from the `bedrock-runtime` response body, and note that `opentelemetry-instrumentation-botocore` is deliberately **not** installed (see `../../setup/auto_instrumentation.md`) — it would trace every low-level AWS call. Instrument the model call by hand as above.

---

## Streaming

Two different latencies matter: time to the first chunk (what the user perceives) and total duration (the span). Record both.

```python
def stream_chat(client, messages: list[dict], *, model: str):
    with tracer.start_as_current_span(
        f"chat {model}",
        kind=SpanKind.CLIENT,
        record_exception=False,
        attributes={
            GENAI_OPERATION_NAME: "chat",
            GENAI_PROVIDER_NAME: "openai",
            GENAI_REQUEST_MODEL: model,
            GENAI_REQUEST_STREAM: True,
        },
    ) as span:
        started = time.perf_counter()
        first_chunk_at: float | None = None
        chunk_count = 0
        captured_chunks: list[str] | None = (
            [] if settings.capture_ai_content else None
        )
        captured_chars = 0
        capture_truncated = False
        error_type: str | None = None
        usage: dict | None = None
        response_model: str | None = None
        response_id: str | None = None

        try:
            stream = client.chat.completions.create(
                model=model,
                messages=messages,
                stream=True,
                # Without this, the final usage chunk is never sent and token
                # counts are silently missing for every streamed call.
                stream_options={"include_usage": True},
            )

            for chunk in stream:
                chunk_count += 1
                if first_chunk_at is None:
                    first_chunk_at = time.perf_counter()
                    span.set_attribute(
                        GENAI_TIME_TO_FIRST_CHUNK, first_chunk_at - started
                    )
                    record_time_to_first_chunk(
                        first_chunk_at - started,
                        operation="chat",
                        provider="openai",
                        request_model=model,
                    )

                response_model = getattr(chunk, "model", None) or response_model
                response_id = getattr(chunk, "id", None) or response_id
                if response_model:
                    span.set_attribute(GENAI_RESPONSE_MODEL, response_model)
                if response_id:
                    span.set_attribute(GENAI_RESPONSE_ID, response_id)

                # Arrives on a final chunk whose `choices` list is empty.
                if chunk.usage is not None:
                    usage = extract_stream_usage(chunk)
                    set_usage_attributes(span, usage)

                text = extract_text_delta(chunk)
                if text:
                    if captured_chunks is not None:
                        remaining = 32_768 - captured_chars
                        if remaining > 0:
                            captured = text[:remaining]
                            captured_chunks.append(captured)
                            captured_chars += len(captured)
                        if len(text) > max(remaining, 0):
                            capture_truncated = True
                    yield text
        except GeneratorExit:
            error_type = "cancelled"
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        except Exception as exc:
            error_type = type(exc).__name__
            span.set_attribute(ERROR_TYPE, error_type)
            raise
        finally:
            # `usage` is None if the stream errored before the final chunk, or
            # if include_usage was not requested — the duration observation is
            # still recorded either way.
            span.set_attribute("app.llm.stream.chunk_count", chunk_count)
            if captured_chunks is not None:
                span.set_attribute(
                    GENAI_OUTPUT_MESSAGES,
                    serialize_text_output("".join(captured_chunks)),
                )
                if error_type:
                    span.set_attribute("app.gen_ai.output.capture_mode", "partial")
                elif capture_truncated:
                    span.set_attribute(
                        "app.gen_ai.output.capture_mode", "truncated"
                    )
            record_model_operation(
                duration_s=time.perf_counter() - started,
                operation="chat",
                provider="openai",
                request_model=model,
                response_model=response_model,
                usage=usage,
                error_type=error_type,
            )
```

The stream fragment requires three provider-specific helpers that must be
defined or imported in the final module: `extract_stream_usage`,
`extract_text_delta`, and the non-streaming `extract_usage` adapter. The
constants and metric recorders it uses are imported in the first fragment.

`stream_options={"include_usage": True}` is not optional — without it the provider sends no token counts at all and the omission is silent. Why, and the equivalent for other providers: `token_usage.md`.

The failure mode specific to streaming *generators* is that **the span never ends**. If the consumer abandons the generator, the `finally` runs only when the generator is closed or garbage-collected. Wrap the caller so the generator is always exhausted or explicitly closed, and confirm on a client-disconnect test that the span ends.

Never create a span per token or per chunk. One span per inference call. Thousands of spans per request overwhelm the Collector and make the trace unreadable.

---

## Application-level retries

If the application retries around the SDK, each attempt should be its own span so you can see that three attempts happened rather than one slow call.

```python
for attempt in range(1, max_attempts + 1):
    try:
        return complete_chat(client, messages, model=model)   # own span per attempt
    except RateLimitError:
        if attempt == max_attempts:
            raise
        time.sleep(backoff(attempt))
```

Put the attempt number on the span if the wrapper accepts it (`app.llm.attempt`), and wrap the whole retry loop in a parent span when the caller needs the total latency in one place.

Retry only transient failures — timeouts, connection errors, rate limits, 5xx. A malformed request retried three times is three identical failures and three times the cost.

---

## Other operations

**This section applies to the LangChain path too.** Embedding and retrieval calls in a RAG agent are made by your own retriever code, not by the model callback, so they need these spans wherever the chat call comes from.

The following fences are **boundary sketches**, not complete templates: the
ellipsis is the service's real provider/vector-store call, which must keep the
error contract, content gating, and response parsing used by that dependency.

Embeddings, retrieval, and reranking are separate spans. Collapsing them into the generation span means you cannot tell a slow vector store from a slow model.

```python
with tracer.start_as_current_span(
    f"embeddings {model}",
    kind=SpanKind.CLIENT,
    record_exception=False,
    attributes={
        GENAI_OPERATION_NAME: "embeddings",
        GENAI_PROVIDER_NAME: "openai",
        GENAI_REQUEST_MODEL: model,
        "app.embedding.input_count": len(texts),
    },
) as span:
    ...
    # Through the writer, like every other usage write in this skill — an
    # embedding call reports input tokens only, and normalize_usage() treats
    # every field as optional, so no special case is needed here.
    set_usage_attributes(span, {"input_tokens": response.usage.prompt_tokens})
```

```python
with tracer.start_as_current_span(
    f"retrieval {data_source_id}",
    record_exception=False,
    attributes={
        GENAI_OPERATION_NAME: "retrieval",
        "gen_ai.data_source.id": data_source_id,
        "gen_ai.request.top_k": top_k,
    },
) as span:
    ...
    span.set_attribute("app.retrieval.result_count", len(docs))
    if docs:
        span.set_attribute("app.retrieval.top_score", docs[0].score)
```

Retrieval query text and document contents are opt-in content, on the same switch as prompts (`content_capture.md`). Safe by default: data source ID, top-k, result count, top score, retriever version. Opt-in only: query text, document text, document metadata, user-specific filters.

If the whole thing is a multi-step workflow, wrap it:

```
POST /ask                          SERVER
  invoke_workflow product_rag      INTERNAL
    embeddings text-embedding-3-small
    retrieval product_docs
    chat gpt-5
```

---

## Checklist

- [ ] One wrapper per provider owns the span and the response parsing.
- [ ] `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model` set at span creation.
- [ ] Response model, response ID, and finish reasons recorded.
- [ ] Response model is also passed to `record_model_operation`; request and response model remain separate.
- [ ] Usage adapted from the provider's shape and passed through `set_usage_attributes()` — `token_usage.md`.
- [ ] Streaming calls request usage explicitly and record `gen_ai.response.time_to_first_chunk`.
- [ ] Streaming chunk count is correct with capture off; captured content is bounded and exists only with capture on.
- [ ] Content capture gated on `CAPTURE_AI_CONTENT`, off by default — `content_capture.md`.
- [ ] `error.type` on failure, no `record_exception`, per `../../conventions/errors.md`.
- [ ] Duration and token metrics recorded on both success and failure paths — `../../metrics/genai.md`.
- [ ] Embeddings and retrieval are their own spans.

---

## Then

- metrics: `../../metrics/genai.md`
- logging: `../../logging/genai.md`
