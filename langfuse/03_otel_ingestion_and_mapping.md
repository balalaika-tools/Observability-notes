# OpenTelemetry Ingestion and Attribute Mapping

Langfuse can ingest OpenTelemetry traces directly over OTLP/HTTP. Use this when:

- your service is not Python or JavaScript/TypeScript;
- your organization already emits OTel spans from many services;
- you want the Collector to route GenAI traces to Langfuse;
- you prefer standard OTel instrumentation and only need Langfuse mapping attributes.

For Python LLM application code, the Langfuse SDK is usually easier. Raw OTLP is the interoperability path.

## Endpoint

Langfuse supports OTLP over HTTP with JSON or protobuf. OTLP/gRPC is not supported.

EU region:

```bash
export OTEL_EXPORTER_OTLP_ENDPOINT="https://cloud.langfuse.com/api/public/otel"
```

Trace-specific endpoint:

```bash
export OTEL_EXPORTER_OTLP_TRACES_ENDPOINT="https://cloud.langfuse.com/api/public/otel/v1/traces"
```

Use the correct base URL for your Langfuse region or self-hosted instance.

## Authentication

Langfuse uses Basic Auth where the username is the public key and the password is the secret key.

```bash
AUTH_STRING="$(printf "%s" "pk-lf-...:sk-lf-..." | base64)"

export OTEL_EXPORTER_OTLP_HEADERS="Authorization=Basic ${AUTH_STRING},x-langfuse-ingestion-version=4"
```

If you configure signal-specific headers:

```bash
export OTEL_EXPORTER_OTLP_TRACES_HEADERS="Authorization=Basic ${AUTH_STRING},x-langfuse-ingestion-version=4"
```

The `x-langfuse-ingestion-version: 4` header enables Langfuse's current fast ingestion path for direct OTel spans.

## Python OTLP Exporter Example

```python
import os
from base64 import b64encode

from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


def configure_langfuse_otlp() -> None:
    auth = b64encode(
        f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
    ).decode()

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "agent-service",
                "service.version": os.getenv("RELEASE", "local"),
                "deployment.environment.name": os.getenv("ENVIRONMENT", "dev"),
            }
        )
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=f"{os.getenv('LANGFUSE_BASE_URL', 'https://cloud.langfuse.com')}/api/public/otel/v1/traces",
                headers={
                    "Authorization": f"Basic {auth}",
                    "x-langfuse-ingestion-version": "4",
                },
            )
        )
    )
    trace.set_tracer_provider(provider)
```

## Span Attributes for Langfuse

Raw OpenTelemetry spans become Langfuse observations. Add Langfuse-prefixed attributes when you want fields mapped into first-class Langfuse concepts.

```python
from opentelemetry import trace

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("llm.generate_answer") as span:
    span.set_attribute("langfuse.observation.type", "generation")
    span.set_attribute("langfuse.trace.name", "chat.answer")
    span.set_attribute("langfuse.user.id", "user_123")
    span.set_attribute("langfuse.session.id", "session_abc")
    span.set_attribute("langfuse.version", "prompt-v17")
    span.set_attribute("langfuse.trace.tags", ["chat", "production"])
    span.set_attribute("langfuse.trace.metadata.tenantTier", "enterprise")
    span.set_attribute("langfuse.observation.input", '{"messages":[{"role":"user","content":"Hi"}]}')
    span.set_attribute("langfuse.observation.output", '{"content":"Hello!"}')
```

For raw OTLP, encode structured input/output values as JSON strings if the exporter does not support nested values.

## Trace-Level Mapping

Common trace-level attributes:

| Langfuse field | OTel attribute |
| --- | --- |
| trace name | `langfuse.trace.name` |
| user ID | `langfuse.user.id` or `user.id` |
| session ID | `langfuse.session.id` or `session.id` |
| release | `langfuse.release` |
| version | `langfuse.version` |
| tags | `langfuse.trace.tags` |
| public trace flag | `langfuse.trace.public` |
| trace input | `langfuse.trace.input` |
| trace output | `langfuse.trace.output` |
| filterable metadata | `langfuse.trace.metadata.<key>` |

For accurate Langfuse filtering and aggregation, propagate important trace-level attributes to every span in the trace. If only a leaf span has `user_id`, metrics grouped by user can become incomplete.

## Observation Mapping

Common observation attributes:

| Langfuse field | OTel attribute |
| --- | --- |
| observation type | `langfuse.observation.type` |
| observation input | `langfuse.observation.input` |
| observation output | `langfuse.observation.output` |
| observation metadata | `langfuse.observation.metadata.<key>` |
| model | `gen_ai.request.model` or Langfuse generation fields from SDK |
| provider | `gen_ai.provider.name` |
| input tokens | `gen_ai.usage.input_tokens` |
| output tokens | `gen_ai.usage.output_tokens` |

Use current OpenTelemetry GenAI semantic convention names for new code, especially `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`, and `gen_ai.usage.*`.

## Propagating Trace Attributes with Baggage

OpenTelemetry trace context connects spans across services. Baggage can carry selected Langfuse trace attributes so downstream services can set the same fields on their spans.

Gateway:

```python
from opentelemetry import baggage, propagate, trace

tracer = trace.get_tracer(__name__)

with tracer.start_as_current_span("gateway.request") as span:
    span.set_attribute("langfuse.trace.name", "agent.answer")
    span.set_attribute("langfuse.user.id", "user_123")
    span.set_attribute("langfuse.session.id", "session_abc")

    ctx = baggage.set_baggage("langfuse.user.id", "user_123")
    ctx = baggage.set_baggage("langfuse.session.id", "session_abc", context=ctx)
    ctx = baggage.set_baggage("langfuse.trace.name", "agent.answer", context=ctx)

    headers: dict[str, str] = {}
    propagate.inject(headers, context=ctx)
```

Downstream service:

```python
from opentelemetry import baggage, propagate, trace

tracer = trace.get_tracer(__name__)


def handle(headers: dict[str, str]) -> None:
    ctx = propagate.extract(headers)
    with tracer.start_as_current_span("agent.execute", context=ctx) as span:
        for key in ("langfuse.user.id", "langfuse.session.id", "langfuse.trace.name"):
            value = baggage.get_baggage(key, context=ctx)
            if value:
                span.set_attribute(key, value)
```

Never copy arbitrary baggage into span attributes. Use an allowlist.

## Collector Routing

A common production pattern sends all traces to the central tracing backend and only LLM or agent traces to Langfuse.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
  batch:
    timeout: 5s
    send_batch_size: 1024
  filter/llm:
    error_mode: ignore
    traces:
      span:
        - 'attributes["gen_ai.operation.name"] == nil and attributes["langfuse.observation.type"] == nil'

exporters:
  otlphttp/langfuse:
    endpoint: https://cloud.langfuse.com/api/public/otel
    headers:
      Authorization: "Basic ${AUTH_STRING}"
      x-langfuse-ingestion-version: "4"
  otlp/main_traces:
    endpoint: traces.example.com:4317
    tls:
      insecure: false

service:
  pipelines:
    traces/main:
      receivers: [otlp]
      processors: [memory_limiter, batch]
      exporters: [otlp/main_traces]
    traces/langfuse:
      receivers: [otlp]
      processors: [memory_limiter, filter/llm, batch]
      exporters: [otlphttp/langfuse]
```

Be careful with span-level filtering. Langfuse needs a root span to construct the trace correctly, so filtering only leaf generations can produce incomplete traces. A safer pattern is to mark an entire workflow as LLM-related at the root and propagate that marker.

## Mapping Pitfalls

- Sending OTLP/gRPC to Langfuse. Use OTLP/HTTP.
- Missing Basic Auth or using secret/public keys in the wrong order.
- Using `LANGFUSE_HOST` in new examples. Use `LANGFUSE_BASE_URL`.
- Setting trace attributes only on one span and expecting complete Langfuse metrics.
- Using legacy `gen_ai.system` for new instrumentation instead of `gen_ai.provider.name`.
- Sending raw prompts, retrieved documents, or user data without privacy review.
- Filtering out the root span in the Collector.
- Treating Langfuse as a metrics backend for all OTel metrics. Langfuse is trace and LLM workflow focused; send operational metrics to a metrics backend.
