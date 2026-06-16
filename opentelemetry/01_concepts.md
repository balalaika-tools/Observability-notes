# OpenTelemetry Concepts

## What OpenTelemetry Is

OpenTelemetry, usually shortened to OTel, is an open-source observability framework and toolkit. It standardizes how applications generate, collect, process, and export telemetry data such as traces, metrics, and logs.

It is important to be precise about what OTel is not:

- It is not a tracing backend like Jaeger.
- It is not a metrics database like Prometheus.
- It is not a dashboarding product like Grafana.
- It is not an LLM observability product like Langfuse.

OpenTelemetry is the instrumentation and pipeline standard in front of those tools. You instrument once, then choose or change your backends through configuration.

## Why It Matters

Before OpenTelemetry, most observability tools required their own SDKs and agents. Switching from one backend to another often meant changing application code. In a microservice system with dozens of services, that is painful and risky.

OTel matters because it separates three concerns:

| Concern | Owned by | Example |
| --- | --- | --- |
| Instrumentation | Your application and libraries | Create spans, metrics, logs. |
| Collection and routing | SDKs and Collectors | Batch, sample, redact, fan out. |
| Storage and visualization | Backends | Langfuse, Prometheus, Grafana, Datadog, Jaeger. |

This separation gives production teams portability, consistent semantics across languages, and a single way to reason about telemetry.

## The Three Main Signals

### Traces

A trace records one execution path through the system. It is made of spans that share one trace ID. Each span describes one operation: an HTTP handler, database query, queue publish, retrieval step, model call, tool execution, or agent step.

```text
trace: user asks a RAG question

api.request
  retrieval.search
    vector_db.query
  prompt.build
  gen_ai.chat gpt-4o-mini
  response.postprocess
```

Spans carry:

- `trace_id`: the ID shared by the whole trace;
- `span_id`: the ID of the current operation;
- `parent_span_id`: the caller span, if there is one;
- `name`: a low-cardinality operation name;
- timestamps and duration;
- attributes such as `http.request.method`, `db.system.name`, `gen_ai.request.model`;
- events for meaningful points in time;
- status and exception information.

Use traces when you need to answer: what happened to this specific request?

### Metrics

Metrics are numerical measurements aggregated over time. They are the right signal for dashboards, SLOs, autoscaling, and alerts.

Common metric instruments:

| Instrument | Use for | Example |
| --- | --- | --- |
| Counter | Monotonic totals | Requests, tokens, errors. |
| UpDownCounter | Values that rise and fall | In-flight requests, queue depth when changed inline. |
| Histogram | Distributions | Latency, prompt size, tokens per response. |
| ObservableGauge | Current value observed by callback | Worker queue length, model cache size. |

Use metrics when you need to answer: how is the system behaving in aggregate?

### Logs

Logs are timestamped event records. OTel can normalize logs and correlate them with traces by adding trace IDs and span IDs. The OpenTelemetry logs signal is stable in the specification, but Python logs support is still marked as Development in the official language status, so treat Python OTel log export as an evolving area. Trace-log correlation through existing logging is still very useful.

Use logs when you need to answer: what did the code say at this point in time?

## Core Concepts

### API vs SDK

OpenTelemetry separates API packages from SDK packages.

Library code should depend on the API only:

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

def rank_documents(query: str, documents: list[str]) -> list[str]:
    with tracer.start_as_current_span("retrieval.rank") as span:
        span.set_attribute("retrieval.document_count", len(documents))
        return sorted(documents, key=lambda d: score(query, d), reverse=True)
```

Application code configures the SDK once at startup:

```python
from opentelemetry import trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

resource = Resource.create({
    "service.name": "chat-api",
    "service.version": "2026.06.16",
    "deployment.environment.name": "production",
})

provider = TracerProvider(resource=resource)
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(endpoint="http://otel-collector:4318/v1/traces")
    )
)
trace.set_tracer_provider(provider)
```

If no SDK is configured, API calls become no-ops. This is why libraries can safely instrument themselves without forcing a backend on users.

### Resources

A resource describes the entity that produced telemetry. Always set `service.name`. Add deployment and runtime attributes that help you filter production data:

```python
Resource.create({
    "service.name": "gateway",
    "service.version": "git-sha-or-semver",
    "service.instance.id": "pod-name-or-host-id",
    "deployment.environment.name": "production",
})
```

Do not put request-specific values such as user IDs into resource attributes. Resources describe the service instance, not the request.

### Context Propagation

Context propagation is what keeps spans connected across service boundaries. For HTTP, the standard is W3C Trace Context:

```text
traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01
tracestate: vendor-specific-list
```

When service A calls service B, service A injects headers. Service B extracts them and starts its server span as a child of the caller span. Without propagation, every service creates disconnected traces.

### Baggage

Baggage is a W3C context mechanism for carrying key-value data across services. It is useful for small, low-sensitivity values that should be available downstream, such as tenant tier, experiment variant, session ID, or an opaque user ID.

Baggage is not the same as span attributes. It is not visible in a backend unless you explicitly copy selected baggage keys into span attributes, metric attributes, or log fields.

Security rule: baggage is sent in HTTP headers. Never put secrets, API keys, emails, raw prompts, or personal data in baggage.

### Exporters

Exporters send telemetry to a destination. The default production transport is OTLP:

- OTLP/gRPC typically uses port `4317`;
- OTLP/HTTP typically uses port `4318`;
- signal-specific HTTP endpoints usually end in `/v1/traces`, `/v1/metrics`, or `/v1/logs`.

Use console exporters for local debugging. Use OTLP exporters for production.

### Processors

Processors sit between telemetry creation and export.

For traces, the most common processor is `BatchSpanProcessor`. It buffers spans and exports them in batches. Use it in production. `SimpleSpanProcessor` exports synchronously when a span ends and is mainly for debugging or tests.

Collector processors can also batch, drop, redact, transform, enrich, or sample telemetry before it reaches a backend.

### Semantic Conventions

Semantic conventions are the shared attribute names for common work. They are what make telemetry portable.

Examples:

- HTTP: `http.request.method`, `http.response.status_code`, `http.route`
- Deployment: `deployment.environment.name`
- GenAI: `gen_ai.operation.name`, `gen_ai.provider.name`, `gen_ai.request.model`, `gen_ai.usage.input_tokens`

Use semantic convention names when they exist. Use your own namespaced attributes for business-specific data, for example `app.tenant.tier` or `rag.retrieval.strategy`.

### Sampling

Sampling reduces trace volume.

Head sampling decides early, usually at trace start. It is cheap and preserves whole traces when parent-aware, but it cannot know whether the trace will later fail or become slow.

Tail sampling decides after seeing most or all spans in a trace. It is better for rules like "keep all errors" or "keep slow traces", but it requires a Collector topology that routes all spans for a trace to the same sampling processor.

Never use sampling as your only source of metrics. Metrics should be emitted independently, because sampled traces cannot accurately represent exact request totals.

## What Production Teams Usually Build

A production OTel setup usually has:

- SDK initialization in every service;
- framework instrumentation for HTTP clients/servers, DBs, queues, and relevant SDKs;
- manual spans around business operations;
- custom metrics for SLOs and business health;
- log correlation with trace IDs;
- an OpenTelemetry Collector for batching, redaction, routing, sampling, and backend fan-out;
- one or more storage backends for traces, metrics, and logs.

For LLM applications, OTel gives you the distributed systems view. Langfuse adds the LLM-specific view: prompts, generations, token usage, cost, sessions, scores, datasets, experiments, and quality workflows.

