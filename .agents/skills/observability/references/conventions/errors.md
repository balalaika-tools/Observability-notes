# The Error Contract

Every code sample in this skill records failures the same way. Use this contract everywhere; do not mix it with the older span-event style.

---

## Why not `record_exception()`

OpenTelemetry is deprecating `Span.add_event()` and `Span.record_exception()`. Exception detail is moving to named log records correlated with the active span, because a log record has its own timestamp, severity, and schema, and can be retained, sampled, and redacted independently of the trace.

Practical consequence: **do not add new span events.** Not for exceptions, not for checkpoints. The span carries status and bounded attributes; the logs pipeline carries the detail.

---

## The contract

Three things happen when an operation fails:

1. the span ends with `ERROR` status;
2. the span carries a low-cardinality `error.type`;
3. the exception detail — message, stack trace — is emitted once, as a named structured log, while the span is still active.

Nothing else. No `str(exc)` in the span status message, no exception message as an attribute, no duplicated log at every call depth.

---

## Case 1 — the exception escapes the span

The common case. Pass `record_exception=False` so the context manager does not create the legacy event, but leave `set_status_on_exception` at its default so it still sets `ERROR` on the way out.

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)


def retrieve_documents(query: str, top_k: int) -> list[dict]:
    with tracer.start_as_current_span(
        "retrieval product_docs",
        # Suppresses the deprecated exception span event. Status is still set
        # to ERROR by the context manager when the exception escapes.
        record_exception=False,
        attributes={
            "gen_ai.operation.name": "retrieval",
            "gen_ai.data_source.id": "product_docs",
            "gen_ai.request.top_k": top_k,
        },
    ) as span:
        try:
            documents = vector_store.search(query, top_k=top_k)
        except Exception as exc:
            # Bounded class name only. Never the message.
            span.set_attribute("error.type", type(exc).__name__)
            raise

        span.set_attribute("app.retrieval.result_count", len(documents))
        return documents
```

The `except` block adds `error.type` and re-raises. It does not log — the logging boundary that finally handles the exception owns that, and logging at every level produces one incident with six stack traces.

## Case 2 — the exception is caught and handled inside the span

The context manager cannot infer failure from a caught exception. Set the status yourself, and only if the operation genuinely failed.

```python
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

import structlog

log = structlog.get_logger(__name__)
tracer = trace.get_tracer(__name__)


def fetch_price(sku: str) -> Decimal | None:
    with tracer.start_as_current_span(
        "fetch price", record_exception=False
    ) as span:
        try:
            return pricing_client.get(sku)
        except TimeoutError as exc:
            span.set_status(Status(StatusCode.ERROR))
            span.set_attribute("error.type", type(exc).__name__)
            # This is the owning boundary, so it emits the exception detail.
            log.error(
                "price_fetch_failed",
                otel_event_name="app.pricing.fetch.failed",
                exc_info=True,
                **{
                    "error.type": type(exc).__name__,
                    "server.address": pricing_client.host,
                },
            )
            return None
```

If a fallback succeeds and the operation as a whole is fine, **do not** mark the span `ERROR`. A successful fallback is a successful operation; record it as `app.fallback.used=true` and leave status unset. Marking it failed makes error-rate alerts and error-biased tail sampling both wrong.

## Case 3 — you started the span manually

Callbacks and middleware often cannot use a context manager. Then you own status, attributes, and `end()`.

```python
span = tracer.start_span("chat gpt-5", attributes=request_attributes)
try:
    response = call_model()
except Exception as exc:
    span.set_status(Status(StatusCode.ERROR))
    span.set_attribute("error.type", type(exc).__name__)
    span.end()
    raise
else:
    span.set_attribute("gen_ai.response.model", response.model)
    span.end()
```

`tracer.start_span()` does **not** set status on exception for you and does **not** make the span current. Use `trace.use_span(span, end_on_exit=False, record_exception=False)` if child spans must nest under it.

---

## `error.type` values

Low-cardinality, and stable enough to alert on.

| Good | Bad |
| --- | --- |
| `TimeoutError` | `TimeoutError: pricing-api timed out after 3.0s` |
| `RateLimitError` | `429 Too Many Requests for sku=ABC-123` |
| `429` (provider status code) | the full response body |
| `_OTHER` for an unclassified failure | `type(exc)` repr |

Prefer the exception class name, or a provider error code when the SDK exposes a stable one. Wrapped exceptions are worth unwrapping — `RetryError` tells you nothing; the cause does.

---

## Where the exception log goes

Emit it at the boundary that decides the request's outcome: the HTTP exception handler, the worker's per-message handler, the job's top-level `try`. That is one record per failed operation with a full stack trace, correlated by `trace_id`/`span_id` to every span in the trace.

The structlog processor that turns these into named OpenTelemetry events, and the duplicate-ingestion guard it needs, are in `../logging/structlog.md`.

### GenAI client exception event versus an application boundary event

When the log record itself represents a failed provider-facing model operation
and the service exports named OTel events, use the standard event name
`gen_ai.client.operation.exception`. When an outer HTTP, job, or agent boundary
logs the overall application failure, use an application-owned event such as
`app.request.failed` or `app.agent.invocation.failed`. Do not emit both for the
same escaping exception merely to satisfy both names; the one-record ownership
rule still wins. A recovered physical model attempt may use the standard event
at warning level because it never reaches the outer boundary.

---

## Making failures visible in both signals

Log severity and span status are independent fields. `logger.error(...)` does not set the active span to `ERROR`, and an `ERROR` span does not create a log. A tail-sampling policy that keeps error traces sees only the span status — so an operation that logged an error but left its span `UNSET` will be sampled away exactly when you need it.

Set both, deliberately, at the boundary that knows the operation failed.

---

## Checklist

- [ ] No `record_exception()` or `add_event()` anywhere in the new code.
- [ ] Every manually created span passes `record_exception=False`.
- [ ] Every failure path sets a bounded `error.type`.
- [ ] Caught-and-handled failures set span status explicitly, and only when the operation actually failed.
- [ ] Exception detail is logged once, at the owning boundary, with `exc_info=True`.
- [ ] Provider-facing GenAI client exception events use `gen_ai.client.operation.exception`; outer application failures use an `app.*` event and are not duplicated.
- [ ] No exception message appears in a span attribute, span status message, or metric attribute.
