# Multi-Service Examples

Distributed tracing only becomes useful when spans from different processes are
connected. This chapter shows how that connection works in practice for Python
services, HTTP calls, queues, baggage, logs, and metrics.

The core mechanism is context propagation:

```text
service A has active span
  -> inject trace context into outbound carrier
  -> service B extracts context from inbound carrier
  -> service B starts a span with service A as remote parent
```

For HTTP, the carrier is usually request headers. For queues, it is usually
message metadata. For gRPC, it is metadata. For custom transports, you choose a
place to carry the context.

## 🗺️ Target Architecture

Example application:

```text
client
  |
  v
gateway service
  |-- receives POST /chat
  |-- starts gateway server span
  |-- attaches safe baggage
  |-- calls retrieval service
  |-- calls agent service
       |
       v
retrieval service
agent service
  |-- extract trace context
  |-- create child server spans
  |-- add manual business spans
  |-- copy allowlisted baggage into attributes
  |-- emit correlated logs and metrics
```

The desired trace:

```text
POST /chat                         gateway SERVER span
  gateway.chat                     gateway INTERNAL span
    POST retrieval /search         gateway CLIENT span
      POST /search                 retrieval SERVER span
        retrieval.search           retrieval INTERNAL span
        vector_db.query            retrieval CLIENT span
    POST agent /run                gateway CLIENT span
      POST /run                    agent SERVER span
        agent.invoke               agent INTERNAL span
          agent.plan               agent INTERNAL span
          chat gpt-4o-mini         model CLIENT span
          execute_tool search_docs tool INTERNAL span
```

The point is not the exact span names. The point is one `trace_id`, clear
service boundaries, and enough business spans to explain the request.

## 🔗 Context vs Baggage

Trace context and baggage travel together, but they mean different things.

| Mechanism | Carries | Used for |
| --- | --- | --- |
| Trace context | `trace_id`, parent span ID, sampled flag, vendor trace state | Connecting spans into one trace. |
| Baggage | Small key-value request facts | Making selected context available downstream. |

Trace context is required for distributed tracing. Baggage is optional.

Good baggage:

- tenant tier;
- experiment variant;
- opaque session ID;
- request class;
- region;
- plan name from a small enum.

Bad baggage:

- email address;
- access token;
- API key;
- raw prompt;
- retrieved document text;
- full request body;
- anything that should not be sent to an external downstream service.

Baggage does not automatically become span attributes. You must explicitly copy
allowlisted baggage keys into spans, metrics, or logs.

> ⚠️ **Watch out:** Baggage values never appear in traces or metrics on their own — if you need a baggage value in a span attribute, you must explicitly copy it, or use a span processor like the one below to do it automatically.

## 🛠️ Shared Baggage Span Processor

A span processor can copy allowlisted baggage values onto spans as they start.
Register this in each service that wants those values to appear in traces.

```python
# baggage_span_processor.py
from __future__ import annotations

from opentelemetry import baggage, context
from opentelemetry.sdk.trace import SpanProcessor


class AllowlistedBaggageSpanProcessor(SpanProcessor):
    """Copy selected baggage values into span attributes at span start."""

    BAGGAGE_KEYS = {
        "app.session.id",
        "app.tenant.tier",
        "app.experiment.variant",
        "app.request.class",
    }

    def on_start(self, span, parent_context=None) -> None:
        ctx = parent_context if parent_context is not None else context.get_current()
        for key in self.BAGGAGE_KEYS:
            value = baggage.get_baggage(key, context=ctx)
            if value is not None:
                span.set_attribute(key, value)

    def on_end(self, span) -> None:
        return None

    def shutdown(self) -> None:
        return None

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        return True
```

Register it before the batch exporter:

```python
provider = TracerProvider(resource=resource)
provider.add_span_processor(AllowlistedBaggageSpanProcessor())
provider.add_span_processor(BatchSpanProcessor(otlp_exporter))
trace.set_tracer_provider(provider)
```

Do not copy every baggage key. Treat baggage from external callers as untrusted.
Use an application namespace such as `app.*`. Use `langfuse.*` only when you
are intentionally setting Langfuse trace or observation fields.

## 🛠️ Gateway With Manual Propagation

This example shows manual injection so the mechanics are visible. If FastAPI and
HTTPX instrumentations are active, they usually create server/client spans and
inject/extract automatically. Do not add manual propagation everywhere unless
you need explicit control.

```python
import httpx
from fastapi import FastAPI, Request
from opentelemetry import baggage, context, trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.propagate import inject

app = FastAPI()
tracer = trace.get_tracer(__name__)
HTTPXClientInstrumentor().instrument()
FastAPIInstrumentor.instrument_app(app)


@app.post("/chat")
async def chat(request: Request) -> dict:
    body = await request.json()
    identity = authenticate_request(request)
    session_id = load_authorized_session(identity, body["session_id"])
    tenant_tier = identity.tenant_tier
    experiment = experiment_service.variant_for(identity.user_id, "chat-prompt")

    # Validate even server-derived values before they become propagating headers.
    session_id = validate_baggage_value(session_id, max_length=128)
    tenant_tier = validate_enum(tenant_tier, {"free", "pro", "enterprise"})
    experiment = validate_enum(experiment, {"control", "prompt-v2"})

    ctx = baggage.set_baggage("app.session.id", session_id)
    ctx = baggage.set_baggage("app.tenant.tier", tenant_tier, context=ctx)
    ctx = baggage.set_baggage("app.experiment.variant", experiment, context=ctx)
    ctx = baggage.set_baggage("app.request.class", "interactive", context=ctx)

    token = context.attach(ctx)
    try:
        with tracer.start_as_current_span("gateway.chat") as span:
            span.set_attribute("http.route", "/chat")
            span.set_attribute("app.request.kind", "chat")

            retrieval_headers: dict[str, str] = {}
            inject(retrieval_headers)

            async with httpx.AsyncClient(timeout=10) as client:
                retrieval_response = await client.post(
                    "http://retrieval:8080/search",
                    headers=retrieval_headers,
                    json={"query": body["message"], "top_k": 5},
                )
                retrieval_response.raise_for_status()
                docs = retrieval_response.json()["documents"]

            agent_headers: dict[str, str] = {}
            inject(agent_headers)

            async with httpx.AsyncClient(timeout=30) as client:
                agent_response = await client.post(
                    "http://agent:8080/run",
                    headers=agent_headers,
                    json={"message": body["message"], "documents": docs},
                )
                agent_response.raise_for_status()

            result = agent_response.json()
            span.set_attribute("app.response.document_count", len(docs))
            return result
    finally:
        context.detach(token)
```

Important details:

- Baggage is attached to the current context before child spans and outbound calls.
- `inject(headers)` serializes trace context and baggage into HTTP headers.
- The request body does not carry tracing metadata.
- `context.detach(token)` prevents baggage from leaking into unrelated requests.

> ⚠️ **Watch out:** Forgetting `context.detach(token)` in an async server causes baggage from one user's request to bleed into concurrent unrelated requests that share the same thread or task context.
- Baggage values are safe categories or opaque IDs, not raw user content.
- FastAPI creates the gateway `SERVER` span; HTTPX creates each outbound `CLIENT` span and also injects context. The explicit `inject()` calls make the carrier visible but are redundant once HTTPX instrumentation is verified.
- `session_id` is length/character validated and ownership checked. Tenant tier and experiment assignment come from trusted server state rather than caller-selected request fields.

## 🛠️ Downstream Service With Manual Extraction

Manual extraction:

```python
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.propagate import extract

app = FastAPI()
tracer = trace.get_tracer(__name__)
FastAPIInstrumentor.instrument_app(app)


@app.post("/search")
async def search(request: Request) -> dict:
    parent_ctx = extract(dict(request.headers))
    body = await request.json()

    with tracer.start_as_current_span("retrieval.search", context=parent_ctx) as span:
        span.set_attribute("http.route", "/search")
        span.set_attribute("rag.top_k", body["top_k"])
        span.set_attribute("rag.retrieval.strategy", "hybrid")

        documents = vector_store.search(body["query"], top_k=body["top_k"])
        span.set_attribute("rag.retrieved_documents", len(documents))
        return {"documents": documents}
```

If FastAPI instrumentation is active, inbound extraction and the HTTP server
span are usually automatic. In that case, prefer this shape:

```python
@app.post("/search")
async def search(request: Request) -> dict:
    body = await request.json()

    with tracer.start_as_current_span("retrieval.search") as span:
        span.set_attribute("rag.top_k", body["top_k"])
        documents = vector_store.search(body["query"], top_k=body["top_k"])
        span.set_attribute("rag.retrieved_documents", len(documents))
        return {"documents": documents}
```

Avoid creating a manual server span if the framework instrumentation already
created one. Add internal business spans under the active server span instead.

## 🔄 Automatic Propagation

With FastAPI and HTTPX instrumentation enabled:

```text
incoming request
  -> FastAPI instrumentation extracts traceparent and baggage
  -> FastAPI creates SERVER span
  -> your handler runs with that span active
  -> HTTPX instrumentation creates CLIENT span
  -> HTTPX injects traceparent and baggage into outbound headers
```

Your application code only adds business spans and attributes:

```python
with tracer.start_as_current_span("prompt.build") as span:
    span.set_attribute("rag.retrieved_documents", len(docs))
    prompt = render_prompt(message=message, docs=docs)
```

This is the usual production shape. Manual `inject()` and `extract()` are for
custom transports, queues, or tests.

## 📦 What Headers Look Like

For HTTP, W3C Trace Context uses `traceparent`:

```text
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
```

Parts:

```text
version: 00
trace_id: 4bf92f3577b34da6a3ce929d0e0e4736
span_id: 00f067aa0ba902b7
trace_flags: 01
```

`tracestate` carries vendor-specific trace state:

```text
tracestate: vendor1=value1,vendor2=value2
```

`baggage` carries request context:

```text
baggage: app.tenant.tier=enterprise,app.experiment.variant=reranker_b
```

Do not log full inbound headers unless you redact first. Headers may contain
credentials and baggage.

> ⚠️ **Watch out:** Logging raw inbound headers leaks authorization tokens, cookies, and the full baggage string — always redact before logging headers in any context.

## 🔗 Correlate Traces, Logs, And Metrics

Inside any service, traces, metrics, and logs should share enough context to
move between them during incidents.

```python
import logging
from opentelemetry import metrics, trace

logger = logging.getLogger(__name__)
meter = metrics.get_meter(__name__)
tracer = trace.get_tracer(__name__)

retrieval_results = meter.create_histogram(
    "rag.retrieved_documents",
    unit="{document}",
    description="Documents returned by retrieval.",
)


def rerank(query: str, documents: list[dict]) -> list[dict]:
    with tracer.start_as_current_span("retrieval.rerank") as span:
        ranked = reranker.rank(query, documents)

        attrs = {"rag.retrieval.strategy": "hybrid_rerank"}
        span.set_attribute("rag.candidate_documents", len(documents))
        span.set_attribute("rag.returned_documents", len(ranked))
        retrieval_results.record(len(ranked), attrs)

        logger.info(
            "reranked documents",
            extra={
                "candidate_documents": len(documents),
                "returned_documents": len(ranked),
            },
        )
        return ranked
```

This gives:

- trace detail for one request;
- metric trends for all requests;
- logs with the active trace and span IDs.

Metric attributes should remain low-cardinality. Do not add trace IDs, user IDs,
prompts, or full queries to metric attributes.

## 🔄 Propagation Through Queues

Queue propagation uses message headers or metadata:

```python
from opentelemetry.propagate import extract, inject


def publish_job(queue, payload: dict) -> None:
    with tracer.start_as_current_span(
        "orders publish",
        kind=trace.SpanKind.PRODUCER,
        attributes={
            "messaging.system": "rabbitmq",
            "messaging.destination.name": "orders",
            "messaging.operation.type": "publish",
        },
    ) as producer:
        headers: dict[str, str] = {}
        # Inject from the active PRODUCER span, not its parent request span.
        inject(headers)
        queue.publish(payload, headers=headers)


def consume_job(message) -> None:
    parent_ctx = extract(message.headers)
    with tracer.start_as_current_span(
        "orders process",
        context=parent_ctx,
        kind=trace.SpanKind.CONSUMER,
        attributes={
            "messaging.system": "rabbitmq",
            "messaging.destination.name": "orders",
            "messaging.operation.type": "process",
        },
    ):
        process(message.payload)
```

This creates a parent-child relationship:

```text
POST /chat
  orders publish             PRODUCER
    orders process           CONSUMER
```

Use this when the worker job is causally part of the same request.

> 💡 **Key insight:** Call `inject(headers)` inside the PRODUCER span (not before it) so the consumer sees the PRODUCER as its parent — injecting from an outer span skips the queue boundary and produces a misleading trace shape.

## 🔗 Span Links For Decoupled Work

Sometimes a queued job is related but not a direct child:

- the job may run much later;
- one batch consumes many messages;
- one message triggers many independent jobs;
- several upstream events contribute to one downstream operation.

Use span links.

```python
from opentelemetry import trace
from opentelemetry.propagate import extract
from opentelemetry.trace import Link


def process_batch(messages: list) -> None:
    links: list[Link] = []

    for message in messages:
        message_ctx = extract(message.headers)
        span_context = trace.get_current_span(message_ctx).get_span_context()
        if span_context.is_valid:
            links.append(Link(span_context))

    with tracer.start_as_current_span("worker.process_batch", links=links) as span:
        span.set_attribute("messaging.batch.message_count", len(messages))
        process_messages(messages)
```

Links say "related to these contexts" without pretending there is one parent.

## 🔄 Async Tasks And Threads

Context usually flows through normal async call paths, but it can be lost when
work moves into background tasks, thread pools, subprocesses, or custom
schedulers.

Pattern for explicit context capture:

```python
from opentelemetry import context


def submit_background_work(executor, payload: dict) -> None:
    current_ctx = context.get_current()
    executor.submit(run_with_context, current_ctx, payload)


def run_with_context(parent_ctx, payload: dict) -> None:
    token = context.attach(parent_ctx)
    try:
        with tracer.start_as_current_span("background.process"):
            process(payload)
    finally:
        context.detach(token)
```

If spans suddenly become roots when work leaves the request handler, suspect
context loss.

> 💡 **Key insight:** When spans unexpectedly start a new trace instead of being children, the root cause is almost always context lost at an async boundary — capture the context explicitly before handing off to a thread pool, executor, or background task.

## 🔗 Propagation Through gRPC And Custom Transports

For gRPC, use gRPC instrumentation where available. It should handle metadata
injection and extraction.

For custom transports, define a carrier:

```python
metadata: dict[str, str] = {}
inject(metadata)
send_message(payload, metadata=metadata)
```

On receive:

```python
parent_ctx = extract(received_metadata)
with tracer.start_as_current_span("custom_transport.handle", context=parent_ctx):
    handle(payload)
```

Requirements for a custom carrier:

- it must preserve `traceparent`;
- it should preserve `tracestate`;
- it should preserve `baggage` only if baggage is safe for that boundary;
- it must not expose tracing metadata to user-visible payloads;
- it should survive serialization and broker hops.

## 🌐 Propagator Configuration

All services should agree on propagators. Common setting:

```bash
export OTEL_PROPAGATORS=tracecontext,baggage
```

If one service uses W3C Trace Context and another only understands a different
format, traces will break at that boundary.

During migrations, some teams configure multiple propagators. Be explicit and
document the compatibility period.

## 🔗 Baggage And Langfuse Context

For LLM applications, you often need user, session, release, prompt, or
experiment context across spans.

General OTel baggage keys:

```text
app.session.id
app.tenant.tier
app.experiment.variant
app.request.class
```

Langfuse-specific trace fields:

```text
langfuse.user.id
langfuse.session.id
langfuse.release
langfuse.trace.metadata.customer_tier
langfuse.observation.metadata.tool_family
```

Use `app.*` when the context is generally useful. Use `langfuse.*` when you are
intentionally mapping fields into Langfuse. Do not put raw prompts or documents
in baggage.

## 🛠️ End-To-End Local Test

To verify propagation locally:

1. Start a Collector with a debug or console exporter.
2. Run gateway, retrieval, and agent services with the same OTLP endpoint.
3. Send one request with a known `session_id`.
4. Inspect the exported spans.

Expected:

- all service spans share one `trace_id`;
- gateway client spans have matching downstream server spans;
- `service.name` differs by service;
- `app.session.id` appears only if copied from baggage;
- logs inside the request include the same trace ID;
- metrics have route/model/outcome labels but not trace ID or user email.

## 🔍 Troubleshooting Broken Traces

Every service starts a new trace:

- inbound extraction is missing;
- outgoing injection is missing;
- services use incompatible propagators;
- proxies strip `traceparent`;
- manual extraction happens too late.

Client span has no matching server span:

- downstream service is not instrumented;
- request went through a proxy that dropped headers;
- service exports to a different backend;
- sampling dropped one side;
- route handler starts a new root span manually.

Baggage missing:

- `OTEL_PROPAGATORS` does not include `baggage`;
- baggage was set after headers were injected;
- context was not attached;
- downstream service did not copy baggage to span attributes;
- baggage key was filtered or too large for headers.

Logs do not correlate:

- logging instrumentation is missing;
- log formatter does not include trace/span IDs;
- logs are emitted outside the active span;
- background worker lost context.

Queue jobs become root traces:

- producer did not inject context into message metadata;
- consumer did not extract metadata;
- broker stripped metadata;
- job is intentionally decoupled and should use span links instead.

## ✅ Design Checklist

- Use auto-instrumentation for standard HTTP/gRPC/database/queue clients.
- Add manual spans around business operations.
- Use manual `inject()` and `extract()` only where auto-instrumentation does not apply.
- Keep baggage small, safe, and allowlisted.
- Copy baggage into span attributes only when needed.
- Use span links for decoupled or batch work.
- Keep metrics independent from traces.
- Confirm trace IDs match across services in a local or staging trace.
- Confirm logs include trace IDs for request-scoped messages.
- Confirm downstream third-party calls do not receive unsafe baggage.
