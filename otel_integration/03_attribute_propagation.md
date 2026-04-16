# Propagating userId, sessionId, and Trace Attributes Across All Spans

> **Who this is for**: Engineers using raw OpenTelemetry instrumentation (not the Langfuse Python SDK) who need Langfuse's filtering, aggregation, and user-level analytics to work correctly across every span in a distributed trace.

---

## 1. The Problem

---

**Langfuse** aggregates and filters your observability data by several trace-level attributes: `userId`, `sessionId`, `tags`, `version`, `release`, and `metadata`. For these aggregations to be accurate, these attributes must be present on **every span** in a trace — not just the root span.

This is where most OTel-only setups silently break.

### Why child spans are missing the context

In OpenTelemetry, span attributes are **local to the span they are set on**. When you call `span.set_attribute("langfuse.user.id", "user-123")` on your root span, that value is recorded on that span's data structure only. Child spans created inside the same `with` block have their own empty attribute maps — they do not inherit from the parent.

```
┌─────────────────────────────────────────────────┐
│  Span A  (name="root-handler")                  │
│  langfuse.user.id = "user-123"          ✅      │
│                                                  │
│  ┌───────────────────────────────────────────┐  │
│  │  Span B  (name="retrieval")               │  │
│  │  langfuse.user.id = MISSING       ❌      │  │
│  │                                            │  │
│  │  ┌─────────────────────────────────────┐  │  │
│  │  │  Generation C  (name="llm-call")    │  │  │
│  │  │  langfuse.user.id = MISSING  ❌     │  │  │
│  │  └─────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────┘  │
└─────────────────────────────────────────────────┘
```

### What this breaks in Langfuse

When you filter the Langfuse dashboard by `userId = "user-123"`, Langfuse evaluates every **observation** (span) independently. Spans B and C above will not appear under that user filter. This causes:

- Cost attribution to be incomplete — only the root span's token counts are attributed to the user
- Session views to be fragmented — some spans appear in the session, others are orphaned
- Evaluation scores attached to child spans to be excluded from user-level rollups

> **Key insight**: The problem is not with Langfuse — it is a fundamental property of OTel's data model. Span attributes are not inherited. You must explicitly propagate context values to every span, or use a mechanism designed for this purpose.

---

## 2. Solution 1 — OTel Baggage API (Recommended for Raw OTel)

---

**Baggage** is a W3C standard mechanism for propagating key-value pairs across span boundaries, across goroutine/thread boundaries, and across service boundaries over HTTP. Unlike span attributes (which are local to one span), baggage is attached to the **context object** and flows automatically to every operation that shares or inherits that context.

### How baggage differs from span attributes

| Concept | Span Attributes | OTel Baggage |
|---|---|---|
| Scope | Single span only | Entire context subtree |
| Cross-service | No (not propagated) | Yes (via HTTP headers) |
| Wire format | Not transmitted | `baggage` HTTP header (W3C) |
| Auto-inherited by child spans | No | Yes (via context propagation) |
| Visible in Langfuse without extra work | Yes | Only if copied to span attributes |

### Setting baggage on the root context

```python
from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor

tracer = trace.get_tracer("my-service")

# Build a context carrying the trace-level attributes as baggage
ctx = baggage.set_baggage("langfuse.user.id", "user-123")
ctx = baggage.set_baggage("langfuse.session.id", "sess-abc", context=ctx)
ctx = baggage.set_baggage("langfuse.release", "v2.1.0", context=ctx)
ctx = baggage.set_baggage("langfuse.trace.tags", "production,feature-x", context=ctx)

# All spans created within this context automatically carry the baggage
with context.use_context(ctx):
    with tracer.start_as_current_span("root-handler") as root:
        # baggage is accessible here
        with tracer.start_as_current_span("retrieval") as child:
            # baggage is still accessible here — same context subtree
            with tracer.start_as_current_span("llm-call") as generation:
                # baggage is accessible here too
                pass
```

> **Key insight**: Baggage travels with the context, not with the span. Any code that runs inside `context.use_context(ctx)` — including code in called functions, async tasks that inherit the context, and downstream HTTP calls — will have access to the baggage values.

⚠️ **Important**: Baggage alone does **not** write values onto span attributes. The spans above will still have empty `langfuse.user.id` fields. Baggage is only the transport mechanism. You need a `BaggageSpanProcessor` (Section 3) to copy baggage into actual span attributes.

---

## 3. Solution 2 — BaggageSpanProcessor (Makes Baggage Flow Into Span Attributes)

---

A **SpanProcessor** is a hook that fires on every `span.start()` and `span.end()` event in the OTel SDK. By writing a custom processor that reads baggage and copies it to span attributes on start, every span in the trace automatically receives the values — without any per-span code.

### Implementation

```python
from opentelemetry import baggage, context, trace
from opentelemetry.sdk.trace import TracerProvider, SpanProcessor
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter


class BaggageSpanProcessor(SpanProcessor):
    """Copies OTel Baggage entries into span attributes on span start.

    This ensures that trace-level context (userId, sessionId, release, tags)
    is present on every span in the trace, not just the root span.
    Langfuse reads these attributes to power user-level filtering and
    session grouping.
    """

    # The baggage keys to propagate. Only copy what you explicitly set.
    BAGGAGE_KEYS = {
        "langfuse.user.id",
        "langfuse.session.id",
        "langfuse.release",
        "langfuse.trace.tags",
    }

    def on_start(self, span, parent_context=None):
        # Prefer the parent_context argument; fall back to the ambient context.
        ctx = parent_context if parent_context is not None else context.get_current()
        for key in self.BAGGAGE_KEYS:
            value = baggage.get_baggage(key, ctx)
            if value is not None:
                span.set_attribute(key, value)

    def on_end(self, span):
        pass  # nothing to do on end

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis: int = 30_000) -> bool:
        return True


# Register with TracerProvider — order matters: BaggageSpanProcessor must
# come BEFORE BatchSpanProcessor so attributes are set before export.
langfuse_exporter = OTLPSpanExporter(
    endpoint="https://cloud.langfuse.com/api/public/otel/v1/traces",
    headers={"Authorization": "Basic <base64-encoded-pk:sk>"},
)

provider = TracerProvider()
provider.add_span_processor(BaggageSpanProcessor())           # 1. copy baggage → attributes
provider.add_span_processor(BatchSpanProcessor(langfuse_exporter))  # 2. batch and export
trace.set_tracer_provider(provider)
```

### What the trace looks like after applying this processor

```
┌─────────────────────────────────────────────────────────────┐
│  Span A  (name="root-handler")                              │
│  langfuse.user.id    = "user-123"   ✅  ← set via baggage   │
│  langfuse.session.id = "sess-abc"   ✅                      │
│                                                              │
│  ┌───────────────────────────────────────────────────────┐  │
│  │  Span B  (name="retrieval")                           │  │
│  │  langfuse.user.id    = "user-123"  ✅  ← processor    │  │
│  │  langfuse.session.id = "sess-abc"  ✅                  │  │
│  │                                                        │  │
│  │  ┌─────────────────────────────────────────────────┐  │  │
│  │  │  Generation C  (name="llm-call")                │  │  │
│  │  │  langfuse.user.id    = "user-123"  ✅            │  │  │
│  │  │  langfuse.session.id = "sess-abc"  ✅            │  │  │
│  │  └─────────────────────────────────────────────────┘  │  │
│  └───────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

💡 **Tip**: Keep `BAGGAGE_KEYS` as a class-level constant so it is easy to audit what you are propagating. Do not use a wildcard approach that copies all baggage — you may unintentionally expose internal or third-party baggage values as Langfuse attributes.

---

## 4. Solution 3 — Langfuse SDK Helper (Simpler for SDK Users)

---

If you are using the **Langfuse Python SDK** rather than raw OTel, the SDK provides higher-level helpers that handle propagation internally. You do not need to implement `BaggageSpanProcessor` yourself.

### Context manager style

```python
from langfuse import get_client

langfuse = get_client()

with langfuse.start_as_current_observation(as_type="span", name="root") as span:
    # propagate_attributes() writes these values into the trace context.
    # All nested observations created within this block will inherit them.
    langfuse.propagate_attributes(
        user_id="user-123",
        session_id="sess-abc",
        metadata={"env": "production", "tenant": "acme"},
        version="v2.1.0",
        tags=["prod", "feature-search"],
    )
    # Nested spans automatically have user_id and session_id set
    with langfuse.start_as_current_observation(as_type="span", name="retrieval"):
        pass
```

### Decorator style

```python
from langfuse.decorators import observe, langfuse_context

@observe()
def root_handler(user_id: str, session_id: str):
    # Update the current trace with user context immediately on entry.
    # All child @observe calls inherit these values.
    langfuse_context.update_current_trace(
        user_id=user_id,
        session_id=session_id,
        tags=["production"],
        metadata={"request_id": "req-xyz"},
    )
    # Call nested functions — they will inherit the trace context.
    retrieval_step()
    generation_step()


@observe()
def retrieval_step():
    # No need to re-set user_id here — it is inherited from root_handler's trace.
    pass


@observe()
def generation_step():
    # Same — user_id and session_id are already on this span.
    pass
```

> **Key insight**: The Langfuse SDK's `update_current_trace()` applies attributes to the **trace** (root span) and propagates them to all child observations within the same trace. It is the recommended approach when you control the full call stack within a single service.

---

## 5. Distributed Tracing Across Services

---

When your trace spans **multiple services** (e.g., an API gateway calls a retrieval service, which calls an LLM proxy), both the trace context (`traceparent`) and baggage must be injected into outgoing HTTP requests and extracted from incoming ones.

```
  Service A                          Service B
  ┌──────────────────┐               ┌──────────────────────┐
  │  root-handler    │               │  service-b-handler   │
  │  baggage:        │   HTTP POST   │  (trace continues)   │
  │  user.id=u-123   │ ─────────────→│  baggage inherited:  │
  │                  │  traceparent  │  user.id=u-123  ✅   │
  │                  │  baggage hdr  │                      │
  └──────────────────┘               └──────────────────────┘
```

### Service A — injecting context into outgoing request

```python
import requests
from opentelemetry.propagate import inject
from opentelemetry import baggage, context, trace

tracer = trace.get_tracer("service-a")

def call_service_b(payload: dict, user_id: str) -> dict:
    # Set baggage before injecting so it is included in the baggage header.
    ctx = baggage.set_baggage("langfuse.user.id", user_id)
    ctx = baggage.set_baggage("langfuse.session.id", "sess-abc", context=ctx)

    headers = {}
    with context.use_context(ctx):
        with tracer.start_as_current_span("call-service-b"):
            # inject() writes both `traceparent` and `baggage` HTTP headers.
            inject(headers)
            response = requests.post(
                "https://service-b/api/process",
                headers=headers,
                json=payload,
            )
    return response.json()
```

### Service B — extracting context from incoming request

```python
from opentelemetry.propagate import extract
from opentelemetry import context, trace

tracer = trace.get_tracer("service-b")

def handle_request(request_headers: dict, body: dict):
    # extract() reads `traceparent` (continues the same trace) and
    # `baggage` (restores userId, sessionId, etc.) from HTTP headers.
    ctx = extract(request_headers)

    with context.use_context(ctx):
        with tracer.start_as_current_span("service-b-handler") as span:
            # This span is a child of Service A's root span (same trace_id).
            # BaggageSpanProcessor will copy langfuse.user.id onto this span.
            process(body)
```

⚠️ **Warning**: The `baggage` HTTP header is transmitted in plaintext. Do **not** store PII (email addresses, names), authentication tokens, or secrets in baggage values. Use opaque identifiers like `user-123` or `sess-abc` rather than raw user data.

---

## 6. What to Propagate

---

Not all context is equally important to propagate. The table below describes each Langfuse attribute, why it matters in child spans, and its relative priority.

| Attribute | Langfuse purpose | Propagate? | Priority |
|---|---|---|---|
| `langfuse.user.id` | User-level analytics, per-user cost and latency attribution, filtering by user | Yes — always | Critical |
| `langfuse.session.id` | Groups multi-turn conversations; enables session replay and per-session scores | Yes — always | Critical |
| `langfuse.release` | Version-based regression detection; compare quality metrics across deployments | Yes | High |
| `langfuse.trace.tags` | Arbitrary labels for filtering by environment, feature flag, or experiment | Yes | Medium |
| `langfuse.trace.metadata.*` | Custom business attributes; not used by Langfuse core but useful for your own queries | Optional | Low |

💡 **Tip**: Start with `langfuse.user.id` and `langfuse.session.id` — these drive the most value in the Langfuse UI. Add `release` and `tags` once those are stable.

---

## 7. Common Pitfalls

---

❌ **Setting `user_id` only on the root span without propagation**

```python
# WRONG — user_id is only on the root span
with tracer.start_as_current_span("root") as root:
    root.set_attribute("langfuse.user.id", "user-123")
    with tracer.start_as_current_span("llm-call") as child:
        # langfuse.user.id is NOT present here
        pass
```

✅ **Correct — use baggage + BaggageSpanProcessor**

```python
# CORRECT — baggage propagates to all child spans via BaggageSpanProcessor
ctx = baggage.set_baggage("langfuse.user.id", "user-123")
with context.use_context(ctx):
    with tracer.start_as_current_span("root"):
        with tracer.start_as_current_span("llm-call"):
            # BaggageSpanProcessor copies langfuse.user.id here automatically
            pass
```

---

❌ **Registering `BaggageSpanProcessor` after `BatchSpanProcessor`**

```python
# WRONG — BatchSpanProcessor may export the span before BaggageSpanProcessor sets attributes
provider.add_span_processor(BatchSpanProcessor(exporter))  # exports first
provider.add_span_processor(BaggageSpanProcessor())        # too late
```

✅ **Correct — `BaggageSpanProcessor` must be registered first**

```python
# CORRECT — attributes are set before the span is queued for export
provider.add_span_processor(BaggageSpanProcessor())        # sets attributes on start
provider.add_span_processor(BatchSpanProcessor(exporter))  # exports on end
```

---

⚠️ **Putting PII or secrets in baggage**

Baggage is transmitted as a plaintext HTTP header (`baggage: langfuse.user.id=user-123, ...`). It is logged by proxies, load balancers, and CDNs. Use opaque identifiers only.

---

❌ **Using `SimpleSpanProcessor` instead of `BatchSpanProcessor` in production**

`SimpleSpanProcessor` exports synchronously on every span end. Combine `BaggageSpanProcessor` with `BatchSpanProcessor` to buffer and export asynchronously.

---

💡 **Set baggage as early as possible** — ideally at the request entry point (HTTP handler, queue consumer, CLI entrypoint). The further down the call stack you set it, the more spans will already have been created without it.

---

## Cross-References

- Previous: [`02_span_mapping.md`](02_span_mapping.md) — how OTel span attributes map to Langfuse trace and observation fields
- Langfuse data model (what `userId`, `sessionId`, and `tags` mean semantically): [`../python_sdk/02_trace_data_model.md`](../python_sdk/02_trace_data_model.md)
- Advanced SDK patterns including scoring and custom evaluation triggers: [`../python_sdk/04_advanced_patterns.md`](../python_sdk/04_advanced_patterns.md)
- Evaluation and scoring workflows that depend on correct user/session attribution: [`../evaluation/`](../evaluation/)
