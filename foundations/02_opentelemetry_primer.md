# OpenTelemetry Primer: Architecture, Signals, and the Collector Pipeline

> **Who this is for**: Engineers who understand observability concepts (logs, metrics, distributed tracing) at a conceptual level but haven't worked directly with OpenTelemetry. This file explains what OTel is, why it exists, how its three signals work, and how its collector pipeline fits into a production LLM observability stack.

---

## 1. The Problem OTel Solved: Instrumentation Fragmentation

Before OpenTelemetry, instrumenting a distributed system meant making a bet. If you chose **Jaeger**, you imported Jaeger client libraries. Zipkin? Zipkin libraries. Datadog? Datadog agent and SDK. These were not compatible. Switching backends meant re-instrumenting your entire application — every service, every framework integration, every span annotation.

Two competing open-source standards tried to fix this:

- **OpenTracing** (CNCF, 2016): a vendor-neutral API for distributed tracing. Libraries could instrument against it without knowing the backend.
- **OpenCensus** (Google, 2018): similar goal, but also covered metrics, and included collection infrastructure.

Both had adoption, neither had won, and they were architecturally incompatible. Teams often had to choose one and live with the consequences.

In 2019, OpenTracing and OpenCensus merged into **OpenTelemetry** (OTel). The CNCF project absorbed the best of both:

- OpenTracing's clean API abstraction model
- OpenCensus's metrics coverage and collector design
- A new, unified **wire protocol** (OTLP) that no predecessor had

> **Key insight**: OTel is not a tracing library. It is a **specification** for telemetry APIs, SDKs, data formats, and collection infrastructure — implemented consistently across 11+ languages. You instrument once against the OTel API; you can route data to any backend at deploy time with zero code changes.

OTel reached **GA stability** in 2021 (traces), 2023 (metrics), and 2024 (logs). It is now the second-largest CNCF project by contributors, after Kubernetes.

---

## 2. Three Signals: Traces, Metrics, Logs

OTel organizes all telemetry into three **signals**. Each signal answers a different question about your system.

| Signal | Question answered | Cardinality | Retention |
|--------|-------------------|-------------|-----------|
| **Traces** | What happened during this specific request? | High (per-request) | Short (days–weeks) |
| **Metrics** | How is the system performing in aggregate? | Low (label sets) | Long (months–years) |
| **Logs** | What events occurred and what were the values? | Medium (per-event) | Medium (weeks–months) |

### 2.1 Traces

A **trace** is the complete record of a distributed request's journey — a directed acyclic graph (DAG) of **spans** that share the same `trace_id`. Every span represents one unit of work: an HTTP handler, a database query, an LLM call.

```
Trace ID: 4bf92f3577b34da6a3ce929d0e0e4736

  [root span] handle_user_request          0ms ──────────────────────── 2340ms
      │
      ├── [child] retrieve_documents        45ms ────── 312ms
      │       └── [child] vector_db_search  50ms ── 280ms
      │
      ├── [child] build_prompt             315ms ─ 322ms
      │
      └── [child] llm.chat                 325ms ────────────────────── 2100ms
              └── [child] stream_response  1800ms ─────────────────────2100ms
```

Each span carries:

| Field | Type | Example |
|-------|------|---------|
| `trace_id` | 128-bit hex | `4bf92f3577b34da6a3ce929d0e0e4736` |
| `span_id` | 64-bit hex | `00f067aa0ba902b7` |
| `parent_span_id` | 64-bit hex or absent (root) | `a3ce929d0e0e4736` |
| `name` | string | `llm.chat` |
| `start_time` | Unix nanoseconds | `1713225600000000000` |
| `end_time` | Unix nanoseconds | `1713225601775000000` |
| `attributes` | `map[string]any` | `{"gen_ai.model": "gpt-4o", "gen_ai.usage.input_tokens": 512}` |
| `events` | list of timed annotations | `[{name: "first_token", timestamp: ...}]` |
| `status` | `OK` / `ERROR` / `UNSET` | `OK` |
| `kind` | `CLIENT` / `SERVER` / `INTERNAL` / `PRODUCER` / `CONSUMER` | `CLIENT` |

The **root span** is the span with no `parent_span_id`. Its duration is the total latency experienced by the user.

### 2.2 Metrics

**Metrics** are aggregated numerical measurements, reported at regular intervals. They answer questions about the health of your system across many requests, not one specific request.

OTel defines three core instrument types:

| Instrument | Description | LLM example |
|-----------|-------------|-------------|
| **Counter** | Monotonically increasing value | Total input tokens processed |
| **Gauge** | Point-in-time snapshot | Active concurrent LLM connections |
| **Histogram** | Distribution of values (supports percentiles) | Request latency, tokens per response |

For LLM applications, the most useful metrics are:

- Token counters (input, output, total) per model, per user
- Request latency histograms (p50, p95, p99)
- Error rate counters (by error type, by model)
- Estimated cost per time window (derived from token counts)

### 2.3 Logs

**Logs** in OTel are structured event records emitted at a point in time. Unlike traces, they are not causally linked by default — but OTel adds `trace_id` and `span_id` fields so logs can be correlated with the trace that was active when they were emitted.

```python
import logging
from opentelemetry.instrumentation.logging import LoggingInstrumentor

# Auto-injects trace_id and span_id into every log record
LoggingInstrumentor().instrument(set_logging_format=True)

logger = logging.getLogger(__name__)
# Output includes: trace_id=4bf92f... span_id=00f067... — auto-injected
logger.info("Retrieved %d documents from vector store", len(docs))
```

> **Key insight**: In LLM observability, traces carry the most value — they capture the full prompt, response, token counts, and latency for every LLM call in context. Metrics aggregate across calls for dashboards. Logs fill in detail that doesn't fit cleanly into span attributes.

---

## 3. Core Concepts: SDK Architecture

Understanding the OTel SDK's internal structure is essential for configuring it correctly.

### 3.1 The API vs. SDK Separation

OTel has a strict separation between its **API** and its **SDK**.

- The **API** (`opentelemetry-api`) defines interfaces: `Tracer`, `Span`, `Meter`, `Logger`. It contains no implementation — calling `trace.get_tracer("my-app")` against only the API returns a no-op tracer.
- The **SDK** (`opentelemetry-sdk`) is the implementation. It provides `TracerProvider`, `MeterProvider`, span processors, exporters, and sampling logic.

This split means **library code should only import the API**. Application code configures the SDK at startup. If no SDK is configured, instrumented library calls cost nearly zero (no-op).

```python
# In a library you publish to PyPI — import only the API
from opentelemetry import trace

tracer = trace.get_tracer(__name__, schema_url="https://opentelemetry.io/schemas/1.23.1")

def fetch_context(query: str) -> list[str]:
    with tracer.start_as_current_span("fetch_context") as span:
        span.set_attribute("retrieval.query", query)
        # ... do work
```

```python
# In the application that USES the library — configure the SDK
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

provider = TracerProvider()
provider.add_span_processor(
    BatchSpanProcessor(OTLPSpanExporter(endpoint="https://cloud.langfuse.com/api/public/otel/v1/traces"))
)
trace.set_tracer_provider(provider)
# Now the library's spans will flow through to Langfuse
```

### 3.2 Context Propagation

**Context propagation** is the mechanism that keeps spans connected across process and network boundaries. Without it, a span created in service B has no way to know it belongs to the same trace as a span in service A.

OTel propagates context by injecting **carrier** data into outgoing requests (HTTP headers, Kafka message headers, gRPC metadata) and extracting it in incoming requests.

The **W3C TraceContext** standard defines two HTTP headers for this:

```
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
             ^^  ^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^^  ^^^^^^^^^^^^^^^^  ^^
           version        trace-id (128-bit)       parent-id (64-bit) flags
tracestate: vendor-specific key=value pairs (optional)
```

The `00f067aa0ba902b7` in `traceparent` becomes the `parent_span_id` of the span created in the downstream service. This is how the DAG is assembled.

```python
import httpx
from opentelemetry.propagate import inject, extract
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

def call_downstream_service(url: str, payload: dict) -> dict:
    headers: dict[str, str] = {}
    inject(headers)  # Adds traceparent/tracestate to headers dict
    
    with tracer.start_as_current_span("http.client", kind=trace.SpanKind.CLIENT) as span:
        span.set_attribute("http.url", url)
        response = httpx.post(url, json=payload, headers=headers)
        span.set_attribute("http.status_code", response.status_code)
        return response.json()
```

OTel also supports **Baggage** — arbitrary key-value pairs propagated alongside trace context. Useful for carrying `user_id` or `session_id` from the entry point to every downstream service. See [`../otel_integration/03_attribute_propagation.md`](../otel_integration/03_attribute_propagation.md) for how this works with Langfuse.

### 3.3 Instrumentation Libraries

An **instrumentation library** wraps an existing framework or client library to automatically create spans. You install it, call `.instrument()`, and spans appear without modifying application code.

```bash
pip install opentelemetry-instrumentation-httpx \
            opentelemetry-instrumentation-sqlalchemy \
            opentelemetry-instrumentation-fastapi
```

```python
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

HTTPXClientInstrumentor().instrument()          # Wraps all httpx calls
SQLAlchemyInstrumentor().instrument(engine=db_engine)
FastAPIInstrumentor.instrument_app(app)         # Wraps FastAPI route handlers
```

For LLM calls specifically, the `opentelemetry-instrumentation-openai` package auto-creates spans with GenAI semantic attributes for every OpenAI SDK call.

### 3.4 Exporters

An **exporter** is the component that serializes telemetry data and sends it to a backend. OTel provides exporters for many backends out of the box:

| Exporter | Backend | Package |
|----------|---------|---------|
| `OTLPSpanExporter` | Any OTLP endpoint (Langfuse, Collector, etc.) | `opentelemetry-exporter-otlp-proto-http` |
| `JaegerExporter` | Jaeger | `opentelemetry-exporter-jaeger` |
| `ZipkinExporter` | Zipkin | `opentelemetry-exporter-zipkin` |
| `ConsoleSpanExporter` | stdout (dev/debugging) | `opentelemetry-sdk` (built-in) |
| `InMemorySpanExporter` | In-process (testing) | `opentelemetry-sdk` (built-in) |

Langfuse accepts **OTLP over HTTP** — use `OTLPSpanExporter` pointing at Langfuse's OTLP endpoint. This is covered in detail in [`../otel_integration/01_otel_backend.md`](../otel_integration/01_otel_backend.md).

### 3.5 Span Processors

A **span processor** sits between span creation and the exporter. It receives spans at `on_start` and `on_end` lifecycle hooks.

| Processor | Behavior | Use case |
|-----------|----------|----------|
| `BatchSpanProcessor` | Buffers spans, flushes in batches | Production — low overhead |
| `SimpleSpanProcessor` | Sends each span synchronously as it ends | Debugging only — adds latency |
| Custom processor | Implement `SpanProcessor` interface | Add attributes, filter spans, redact PII |

⚠️ **Never use `SimpleSpanProcessor` in production**. It calls the exporter synchronously in your application's request path. One slow or unreachable backend stalls your entire service.

```python
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

# BatchSpanProcessor configuration for production
processor = BatchSpanProcessor(
    span_exporter=OTLPSpanExporter(endpoint="https://cloud.langfuse.com/api/public/otel/v1/traces"),
    max_queue_size=2048,           # Max spans buffered in memory
    max_export_batch_size=512,     # Spans per export request
    export_timeout_millis=30_000,  # 30s timeout per export attempt
    schedule_delay_millis=5_000,   # Flush every 5s (or when batch is full)
)
```

---

## 4. The OTLP Protocol

**OTLP** (OpenTelemetry Protocol) is the standard wire format for OTel data. It replaced a zoo of Jaeger, Zipkin, and Prometheus-specific protocols with one unified format.

OTLP is defined as **Protocol Buffers schemas** with multiple transport options:

| Transport | Content-Type | Port | Use when |
|-----------|-------------|------|----------|
| gRPC | `application/grpc` | 4317 | High-throughput, microservices, keep-alive connections |
| HTTP/Protobuf | `application/x-protobuf` | 4318 | Firewalls block gRPC, serverless environments |
| HTTP/JSON | `application/json` | 4318 | Debugging, curl testing, low-volume |

Langfuse's OTLP endpoint accepts **HTTP/Protobuf and HTTP/JSON** at:

```
https://cloud.langfuse.com/api/public/otel/v1/traces    # Traces
https://cloud.langfuse.com/api/public/otel/v1/metrics   # Metrics
https://cloud.langfuse.com/api/public/otel/v1/logs      # Logs
```

Authentication is **HTTP Basic Auth** (`pk-...`:`sk-...` as username:password, base64-encoded in the `Authorization` header).

💡 **Testing OTLP/HTTP with curl**:

```bash
# Verify your Langfuse endpoint and credentials accept spans
curl -X POST https://cloud.langfuse.com/api/public/otel/v1/traces \
  -H "Content-Type: application/json" \
  -H "Authorization: Basic $(echo -n 'pk-lf-...:sk-lf-...' | base64)" \
  -d '{
    "resourceSpans": [{
      "resource": {
        "attributes": [{"key": "service.name", "value": {"stringValue": "test-service"}}]
      },
      "scopeSpans": [{
        "scope": {"name": "manual-test"},
        "spans": [{
          "traceId": "4bf92f3577b34da6a3ce929d0e0e4736",
          "spanId": "00f067aa0ba902b7",
          "name": "test-span",
          "kind": 1,
          "startTimeUnixNano": "1713225600000000000",
          "endTimeUnixNano": "1713225601000000000",
          "status": {"code": 1}
        }]
      }]
    }]
  }'
```

---

## 5. The OTel Collector Pipeline

The **OTel Collector** is an optional but powerful intermediary between your application and backends. It is a standalone process (or sidecar container) that receives telemetry, transforms it, and fans it out to multiple destinations.

```
┌─────────────────────────────────────────────────────────────────┐
│                        Your Application                         │
│                                                                 │
│  ┌──────────────┐   ┌──────────────┐   ┌──────────────────┐   │
│  │ FastAPI App  │   │  RAG Service │   │  Eval Worker     │   │
│  │ OTel SDK     │   │  OTel SDK    │   │  OTel SDK        │   │
│  └──────┬───────┘   └──────┬───────┘   └────────┬─────────┘   │
└─────────┼─────────────────┼───────────────────────┼────────────┘
          │   OTLP/gRPC     │   OTLP/gRPC           │ OTLP/HTTP
          └─────────────────┴───────────────────────┘
                                    │
                                    ▼
          ┌─────────────────────────────────────────┐
          │           OTel Collector                │
          │                                         │
          │  ┌───────────┐                          │
          │  │ Receivers │  otlp (gRPC + HTTP)      │
          │  └─────┬─────┘  filelog, prometheus,... │
          │        │                                │
          │  ┌─────▼──────┐                         │
          │  │ Processors │  batch, memory_limiter,  │
          │  └─────┬──────┘  resourcedetection,     │
          │        │         tail_sampling, redact   │
          │  ┌─────▼──────┐                         │
          │  │ Exporters  │  otlphttp, prometheusrw  │
          │  └─────┬──────┘  loki, debug,...         │
          └────────┼────────────────────────────────┘
                   │
          ┌────────┼────────────────────────────────┐
          │        │                                │
          ▼        ▼                                ▼
  ┌──────────┐  ┌──────────┐              ┌──────────────┐
  │ Langfuse │  │ Jaeger   │              │  Prometheus  │
  │ (traces) │  │ (traces) │              │  (metrics)   │
  └──────────┘  └──────────┘              └──────────────┘
```

### 5.1 Collector Components

**Receivers** accept data in various formats and convert it to OTel's internal representation:

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
  filelog:
    include: [/var/log/app/*.log]
    operators:
      - type: json_parser
```

**Processors** transform, filter, or enrich spans in flight:

```yaml
processors:
  batch:
    send_batch_size: 1024
    timeout: 10s
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
  resource:
    attributes:
      - key: deployment.environment
        value: production
        action: upsert
  # Tail sampling: only keep traces with errors or high latency
  tail_sampling:
    decision_wait: 10s
    policies:
      - name: errors-policy
        type: status_code
        status_code: {status_codes: [ERROR]}
      - name: slow-traces-policy
        type: latency
        latency: {threshold_ms: 5000}
```

**Exporters** send processed data to backends:

```yaml
exporters:
  otlphttp/langfuse:
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${env:LANGFUSE_AUTH_HEADER}"  # base64(pk:sk)
  otlphttp/jaeger:
    endpoint: http://jaeger:4318
  prometheusremotewrite:
    endpoint: http://prometheus:9090/api/v1/write
```

**Service pipelines** wire receivers → processors → exporters:

```yaml
service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch, resource, tail_sampling]
      exporters: [otlphttp/langfuse, otlphttp/jaeger]
    metrics:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [prometheusremotewrite]
```

> **Key insight**: The Collector is not mandatory. For simple setups, your app can export directly to Langfuse via OTLP. The Collector becomes valuable when you need **fan-out** (send to multiple backends), **tail sampling** (decide after the full trace is received), or **PII redaction** (strip prompt content before it leaves your network).

---

## 6. Python SDK: End-to-End Setup

A production-ready OTel setup in Python has four parts: resource definition, provider configuration, instrumentation, and graceful shutdown.

```python
import atexit
import os
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.resources import Resource, SERVICE_NAME, SERVICE_VERSION
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

def configure_otel(service_name: str, service_version: str) -> TracerProvider:
    """
    Configure OpenTelemetry for a production application.
    
    Call this once at application startup, before any request handling.
    The TracerProvider is set globally, so all tracer.get_tracer() calls
    throughout the app share the same provider and exporters.
    """
    # Resource describes the entity producing telemetry.
    # service.name and service.version appear as filter dimensions in Langfuse.
    resource = Resource.create({
        SERVICE_NAME: service_name,
        SERVICE_VERSION: service_version,
        "deployment.environment": os.getenv("ENVIRONMENT", "development"),
        "service.instance.id": os.getenv("POD_NAME", "local"),
    })

    # Construct Basic Auth header for Langfuse
    import base64
    pk = os.environ["LANGFUSE_PUBLIC_KEY"]
    sk = os.environ["LANGFUSE_SECRET_KEY"]
    auth = base64.b64encode(f"{pk}:{sk}".encode()).decode()

    exporter = OTLPSpanExporter(
        endpoint=f"{os.getenv('LANGFUSE_HOST', 'https://cloud.langfuse.com')}/api/public/otel/v1/traces",
        headers={"Authorization": f"Basic {auth}"},
    )

    provider = TracerProvider(resource=resource)
    provider.add_span_processor(
        BatchSpanProcessor(
            exporter,
            max_queue_size=2048,
            max_export_batch_size=512,
            export_timeout_millis=30_000,
            schedule_delay_millis=5_000,
        )
    )

    # Register as the global provider — affects all trace.get_tracer() calls
    trace.set_tracer_provider(provider)

    # Auto-instrument outbound HTTP — every httpx call becomes a CLIENT span
    HTTPXClientInstrumentor().instrument()

    # Ensure buffered spans are flushed before the process exits
    atexit.register(provider.shutdown)

    return provider
```

### 6.1 Creating Spans Manually

```python
import time
from opentelemetry import trace
from opentelemetry.trace import Status, StatusCode

tracer = trace.get_tracer(__name__, schema_url="https://opentelemetry.io/schemas/1.23.1")

def run_rag_pipeline(user_query: str, user_id: str) -> str:
    """
    A RAG pipeline that produces three linked spans:
      root → retrieval → generation
    
    Because each child uses start_as_current_span inside the parent's
    context, the parent_span_id is set automatically via Context.
    """
    with tracer.start_as_current_span("rag.pipeline") as root:
        root.set_attribute("user.id", user_id)
        root.set_attribute("rag.query", user_query)

        try:
            with tracer.start_as_current_span("rag.retrieval") as retrieval_span:
                docs = retrieve_documents(user_query)
                retrieval_span.set_attribute("retrieval.document_count", len(docs))
                retrieval_span.set_attribute("retrieval.source", "pgvector")

            with tracer.start_as_current_span("rag.generation") as gen_span:
                prompt = build_prompt(user_query, docs)
                gen_span.set_attribute("gen_ai.system", "openai")
                gen_span.set_attribute("gen_ai.request.model", "gpt-4o")
                gen_span.set_attribute("gen_ai.request.temperature", 0.7)

                response = call_llm(prompt)

                gen_span.set_attribute("gen_ai.usage.input_tokens", response.usage.prompt_tokens)
                gen_span.set_attribute("gen_ai.usage.output_tokens", response.usage.completion_tokens)
                gen_span.set_status(Status(StatusCode.OK))

            root.set_status(Status(StatusCode.OK))
            return response.choices[0].message.content

        except Exception as exc:
            # Record the exception and mark the span as errored
            root.record_exception(exc)
            root.set_status(Status(StatusCode.ERROR, description=str(exc)))
            raise
```

### 6.2 Adding Span Events

**Span events** are time-stamped annotations on a span. They are useful for recording milestones within a long-running span (e.g., first token received during streaming).

```python
import time
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

def stream_llm_response(prompt: str) -> str:
    with tracer.start_as_current_span("llm.stream") as span:
        span.set_attribute("gen_ai.request.model", "gpt-4o")
        span.set_attribute("llm.stream.enabled", True)

        first_token_received = False
        chunks: list[str] = []

        for chunk in openai_client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            stream=True,
        ):
            content = chunk.choices[0].delta.content or ""
            if content and not first_token_received:
                # Record the exact time the first token arrived
                span.add_event(
                    "first_token",
                    attributes={"llm.stream.first_token_latency_ms": ...},
                    timestamp=time.time_ns(),
                )
                first_token_received = True
            chunks.append(content)

        full_response = "".join(chunks)
        span.set_attribute("gen_ai.response.finish_reason", "stop")
        return full_response
```

---

## 7. Why OTel Matters for LLM Observability

General APM tools were designed for RPC calls with predictable structure. LLM applications break those assumptions in several ways:

| Characteristic | Traditional service | LLM application |
|----------------|---------------------|-----------------|
| Latency profile | Milliseconds, predictable | Seconds to minutes, variable |
| Output type | Structured data | Unstructured text |
| Cost signal | CPU/memory | Token consumption |
| Failure modes | Exceptions, timeouts | Hallucinations, policy violations |
| Call depth | Shallow (1–3 hops) | Deep (retrieval → rerank → prompt → generation → post-process) |

OTel handles all of these if you use it correctly:

**Token tracking**: The OTel GenAI Semantic Conventions (covered in [`03_otel_genai_semantics.md`](03_otel_genai_semantics.md)) define standard attribute names for `gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`, and `gen_ai.usage.total_tokens`. Langfuse reads these attributes to display token counts and compute costs without any Langfuse-specific code in your instrumentation.

**Latency decomposition**: In a RAG pipeline, you often can't tell from end-to-end latency whether slowness is in retrieval, reranking, or generation. OTel span start/end times give you exact latency per operation, automatically.

**Context propagation across async pipelines**: If a generation job is queued and executed in a worker process, baggage lets you carry `user_id`, `session_id`, and `trace_id` into the worker without passing them through every function signature.

**Vendor neutrality**: Your instrumentation code doesn't import `langfuse`. It imports `opentelemetry`. If you later want to send to Datadog, Honeycomb, or Grafana Tempo in addition to Langfuse, you add an exporter or Collector rule — zero code changes.

```
Your instrumented code
        │
        │  opentelemetry-sdk (vendor-neutral)
        │
        ▼
┌────────────────────────────────────────┐
│           TracerProvider               │
│                                        │
│  BatchSpanProcessor                    │
│       │                                │
│  ┌────┴─────┐   ┌────────────────┐    │
│  │ Exporter │   │    Exporter    │    │
│  │ Langfuse │   │   Datadog      │    │
│  └────┬─────┘   └───────┬────────┘    │
└───────┼─────────────────┼─────────────┘
        │                 │
        ▼                 ▼
  Langfuse Cloud    Datadog APM
  (LLM traces +    (infra + APM
   evaluations)     metrics)
```

> **Key insight**: With OTel, the **instrumentation is a one-time investment**. The routing, transformation, and destination are **operational decisions** made outside the codebase — in environment variables, Collector config, or CI/CD parameters.

---

## 8. Semantic Conventions and the GenAI Working Group

OTel spans become truly useful across organizations when their attribute names are standardized. A span with `model: gpt-4o` and a span with `gen_ai.request.model: gpt-4o` are semantically identical but incompatible with tools that expect the standard name.

**Semantic Conventions** are OTel's answer to this. They define standard attribute names for common operations — HTTP, database, messaging, and, since 2024, **Generative AI**.

The GenAI conventions cover:

- Model system (`gen_ai.system`: `openai`, `anthropic`, `cohere`, `vertex_ai`, ...)
- Request parameters (`gen_ai.request.model`, `gen_ai.request.temperature`, `gen_ai.request.max_tokens`)
- Usage (`gen_ai.usage.input_tokens`, `gen_ai.usage.output_tokens`)
- Prompt/completion content (as span events, not attributes — to avoid attribute size limits)
- Operation type (`gen_ai.operation.name`: `chat`, `text_completion`, `embeddings`)

Langfuse reads these standard attributes directly. If your instrumentation follows the GenAI conventions, Langfuse surfaces token counts, costs, and model metadata in its UI automatically.

✅ Use `gen_ai.usage.input_tokens` (OTel GenAI convention)
❌ Use `prompt_tokens` or `llm.token_count.prompt` (non-standard, requires custom mapping)

The full attribute schema is covered in [`03_otel_genai_semantics.md`](03_otel_genai_semantics.md).

---

## 9. Common Pitfalls

**Pitfall 1: Not calling `provider.shutdown()` before process exit**

`BatchSpanProcessor` holds spans in memory until it flushes. If the process exits without calling `shutdown()`, the last batch is dropped. Use `atexit.register(provider.shutdown)` or a framework lifecycle hook.

**Pitfall 2: Using `SimpleSpanProcessor` in production**

```python
# ❌ Blocks the request thread for every span export
provider.add_span_processor(SimpleSpanProcessor(OTLPSpanExporter(...)))

# ✅ Non-blocking batch export
provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(...)))
```

**Pitfall 3: Forgetting to propagate context across threads**

Python's `contextvars.Context` is not automatically inherited by `ThreadPoolExecutor` threads. If you spawn threads from within a span, the child threads lose the active span and can't link their spans to the parent trace.

```python
# ❌ Thread loses trace context
with tracer.start_as_current_span("parent"):
    future = executor.submit(child_function)  # child_function sees no active span

# ✅ Explicitly copy the current context into the thread
import contextvars
ctx = contextvars.copy_context()
future = executor.submit(ctx.run, child_function)
```

**Pitfall 4: Setting attributes after the span has ended**

```python
# ❌ Span.set_attribute() after end() is silently ignored
with tracer.start_as_current_span("my-span") as span:
    result = do_work()
# span is now ended

span.set_attribute("result.count", len(result))  # Silently dropped!

# ✅ Set attributes before the context manager exits
with tracer.start_as_current_span("my-span") as span:
    result = do_work()
    span.set_attribute("result.count", len(result))  # ✅
```

**Pitfall 5: Logging secrets in span attributes**

Span attributes are serialized and sent to backends. Never set `gen_ai.prompt` or `gen_ai.completion` as span attributes in a Collector pipeline that touches a third party. Use span events (which can be filtered at the Collector level) or use a custom `SpanProcessor` to redact sensitive fields.

```python
# ⚠️ Prompt content goes to every exporter in the pipeline
span.set_attribute("gen_ai.prompt", full_prompt_with_pii)

# 💡 Use a span event instead — easier to filter at the Collector
span.add_event("gen_ai.content.prompt", {"gen_ai.prompt.role": "user", "gen_ai.prompt.content": full_prompt})
```

---

**Next**: [Part 3: OTel GenAI Semantic Conventions](03_otel_genai_semantics.md)
