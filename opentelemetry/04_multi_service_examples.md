# Multi-Service Examples

This page shows practical propagation patterns for a gateway service calling internal services. The examples use Python and HTTP, but the same concepts apply to queues and gRPC.

## Architecture

```text
client
  |
  v
gateway service
  |-- sets user/session baggage
  |-- starts root server span
  |-- calls retrieval service
  |-- calls agent service
       |
       v
retrieval service
agent service
  |-- extracts trace context
  |-- copies allowlisted baggage to span attributes
  |-- emits logs and metrics with the same context
```

The goal is one trace across all services:

```text
gateway /chat
  http POST retrieval /search
    retrieval.search
  http POST agent /run
    agent.plan
    gen_ai.chat
    agent.tool.execute
```

## Shared Baggage Span Processor

Baggage travels across services, but it does not automatically become span attributes. Add a small span processor to copy allowlisted baggage keys onto each span.

```python
# baggage_span_processor.py
from __future__ import annotations

from opentelemetry import baggage, context
from opentelemetry.sdk.trace import SpanProcessor


class AllowlistedBaggageSpanProcessor(SpanProcessor):
    """Copy selected baggage values into span attributes at span start."""

    BAGGAGE_KEYS = {
        "app.user.id",
        "app.session.id",
        "app.tenant.tier",
        "app.experiment.variant",
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

Use an application namespace (`app.*`) for business context. Use `langfuse.*` keys only when you are explicitly targeting Langfuse trace fields.

## Gateway: Set Baggage And Inject Headers

```python
import httpx
from fastapi import FastAPI, Request
from opentelemetry import baggage, context, trace
from opentelemetry.propagate import inject

app = FastAPI()
tracer = trace.get_tracer(__name__)


@app.post("/chat")
async def chat(request: Request) -> dict:
    body = await request.json()
    user_id = request.headers.get("x-user-id", "anonymous")
    session_id = body["session_id"]

    ctx = baggage.set_baggage("app.user.id", user_id)
    ctx = baggage.set_baggage("app.session.id", session_id, context=ctx)
    ctx = baggage.set_baggage("app.tenant.tier", body.get("tier", "free"), context=ctx)

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

Auto-instrumentation for FastAPI and HTTPX can inject/extract automatically. Manual `inject()` is useful when you need explicit control or are using a transport that is not instrumented.

## Downstream Service: Extract Headers

```python
from fastapi import FastAPI, Request
from opentelemetry import trace
from opentelemetry.propagate import extract

app = FastAPI()
tracer = trace.get_tracer(__name__)


@app.post("/search")
async def search(request: Request) -> dict:
    ctx = extract(dict(request.headers))
    body = await request.json()

    with tracer.start_as_current_span("retrieval.search", context=ctx) as span:
        span.set_attribute("http.route", "/search")
        span.set_attribute("rag.top_k", body["top_k"])

        documents = vector_store.search(body["query"], top_k=body["top_k"])
        span.set_attribute("rag.retrieved_documents", len(documents))
        return {"documents": documents}
```

If the FastAPI instrumentation is active, extraction and server-span creation are usually automatic. You still add manual spans for business steps.

## Correlate Traces, Logs, And Metrics

Inside any service, the active trace context can be used by logs and metrics.

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

        logger.info("reranked documents", extra={"returned_documents": len(ranked)})
        return ranked
```

This gives you:

- trace detail for one request;
- metric trends for all requests;
- logs that include the current trace ID and span ID.

## Propagation Through Queues

For a queue, inject context into message headers before publish and extract it in the consumer.

```python
from opentelemetry.propagate import extract, inject


def publish_job(queue, payload: dict) -> None:
    headers: dict[str, str] = {}
    inject(headers)
    queue.publish(payload, headers=headers)


def consume_job(message) -> None:
    ctx = extract(message.headers)
    with tracer.start_as_current_span("worker.process_job", context=ctx):
        process(message.payload)
```

Use span links instead of parent-child relationships when the queued work is intentionally decoupled or may start long after the original request completes.

## Baggage Guidelines

Good baggage values:

- opaque user ID;
- session ID;
- tenant tier;
- experiment variant;
- region;
- request class.

Bad baggage values:

- email address;
- access token;
- prompt text;
- retrieved document content;
- raw request body;
- anything a downstream third-party API should not see.

Keep baggage small. Many proxies and servers enforce header size limits.

