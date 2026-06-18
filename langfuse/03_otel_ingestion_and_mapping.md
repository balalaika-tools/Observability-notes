# OpenTelemetry Ingestion and Attribute Mapping

Last verified against official Langfuse OpenTelemetry documentation on 2026-06-18.

## Mental Model

Raw OpenTelemetry ingestion is the interoperability path into Langfuse. Instead of using the Python or JS/TS SDK helpers, you emit normal OpenTelemetry spans and add enough `langfuse.*` and `gen_ai.*` attributes for Langfuse to map those spans into traces, observations, generations, metadata, and metrics.

Use this mental model:

```text
application spans
  -> OTel SDK or auto-instrumentation
  -> optional Collector routing/filtering/redaction
  -> OTLP/HTTP endpoint at Langfuse
  -> Langfuse maps spans into LLM traces and observations
```

This solves polyglot and platform-owned telemetry. It does not give you the Python SDK's convenience methods for prompt management, datasets, experiments, or simple scoring. For those, use the Langfuse SDK/API from the services or workers that need them.

Langfuse can ingest OpenTelemetry traces directly over OTLP/HTTP. Use this when:

- your service is not Python or JavaScript/TypeScript;
- your organization already emits OTel spans from many services;
- you want the Collector to route GenAI traces to Langfuse;
- you prefer standard OTel instrumentation and only need Langfuse mapping attributes.

For Python LLM application code, the Langfuse SDK is usually easier. Raw OTLP is the interoperability path.

## Lifecycle of a Raw OTLP Trace

1. Your service starts a root span for the user-visible workflow.
2. The service sets trace-level Langfuse attributes such as `langfuse.trace.name`, `langfuse.user.id`, `langfuse.session.id`, `langfuse.release`, `langfuse.version`, tags, and filterable metadata.
3. Child spans record operations: retrievers, generations, tools, guardrails, chains, agents, and ordinary service work.
4. Generation spans use current OpenTelemetry GenAI attributes plus explicit Langfuse attributes when needed.
5. Trace context travels across services with W3C headers.
6. Selected Langfuse attributes travel with baggage or are copied to spans by a processor, so filtering and aggregation work across observations.
7. The OTel SDK or Collector sends complete traces to Langfuse over OTLP/HTTP.
8. Langfuse maps spans into observations, derives trace attributes, and makes the trace available for inspection, scoring, datasets, and metrics.

The hard part is not exporting bytes. The hard part is preserving a complete trace with the attributes Langfuse needs to group and filter it.

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

Use the correct base URL for your Langfuse region or self-hosted instance. Current Langfuse Cloud regions include EU (`https://cloud.langfuse.com`), US (`https://us.cloud.langfuse.com`), Japan (`https://jp.cloud.langfuse.com`), and HIPAA (`https://hipaa.cloud.langfuse.com`).

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

Security rules:

- Store `AUTH_STRING` as a secret or inject it at runtime; do not commit encoded keys.
- Keep Collector receivers private.
- Use TLS to Langfuse and TLS/mTLS or network policy between applications and Collectors.
- Treat OTel headers as credentials when they contain Langfuse Basic Auth.

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

This example creates a direct exporter from one Python process to Langfuse. In production, many teams export to a Collector first so redaction, retry, batching, and routing policy are centralized.

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

Layered explanation:

- Beginner intuition: attributes are the labels and payloads Langfuse uses to understand a span.
- Technical mechanics: `langfuse.*` attributes take precedence for Langfuse mapping; GenAI semantic conventions fill in model and usage details when explicit Langfuse attributes are absent.
- Production implications: attribute names become a contract across services and dashboards.
- Common mistakes: relying on arbitrary OTel attributes to become filterable metadata, sending arrays/maps through exporters that do not support them, and forgetting to set observation type on custom LLM spans.

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

Layered explanation:

- Beginner intuition: trace-level attributes are the shared facts about the whole workflow.
- Technical mechanics: Langfuse can detect trace attributes on spans, but filters and aggregations increasingly operate at observation level, so every relevant span should carry them.
- Production implications: use a consistent propagation strategy at the root of every request.
- Common mistakes: setting attributes only on the root span, setting them only on the generation span, or storing values as nested metadata that cannot be filtered.

## Observation Mapping

Common observation attributes:

| Langfuse field | OTel attribute |
| --- | --- |
| observation type | `langfuse.observation.type` |
| observation level | `langfuse.observation.level` or span status |
| status message | `langfuse.observation.status_message` or span status message |
| observation input | `langfuse.observation.input` |
| observation output | `langfuse.observation.output` |
| observation metadata | `langfuse.observation.metadata.<key>` |
| model | `langfuse.observation.model.name`, `gen_ai.request.model`, `gen_ai.response.model`, `llm.model_name`, or `model` |
| provider | `gen_ai.provider.name` |
| model parameters | `langfuse.observation.model.parameters`, `gen_ai.request.*`, or `llm.invocation_parameters.*` |
| input tokens | `gen_ai.usage.input_tokens` |
| output tokens | `gen_ai.usage.output_tokens` |
| usage details | `langfuse.observation.usage_details` or `gen_ai.usage.*` |
| cost details | `langfuse.observation.cost_details` or `gen_ai.usage.cost` |
| prompt link | `langfuse.observation.prompt.name` and `langfuse.observation.prompt.version` |
| environment | `langfuse.environment`, `deployment.environment`, or `deployment.environment.name` |

Use current OpenTelemetry GenAI semantic convention names for new code, especially `gen_ai.provider.name`, `gen_ai.operation.name`, `gen_ai.request.model`, and `gen_ai.usage.*`.

Layered explanation:

- Beginner intuition: observation attributes describe one step, not the whole workflow.
- Technical mechanics: a model attribute or `langfuse.observation.type="generation"` tells Langfuse to treat a span as a generation.
- Production implications: accurate observation mapping drives trace readability, token/cost charts, and model-level comparisons.
- Common mistakes: missing `usage_details`, using only legacy GenAI fields in new code, and putting step-specific metadata on the trace.

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

Recommended production options:

- In SDK-based Python/JS services, use Langfuse `propagate_attributes()` / `propagateAttributes()` where available.
- In raw OTel services, set baggage at the workflow entry point and configure a baggage-to-span-attributes processor or explicit allowlist copying.
- In multi-service systems, instrument HTTP clients and servers so W3C trace context and baggage are injected/extracted automatically.
- Keep baggage values short, non-sensitive, and stable.

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

Architecture responsibilities:

| Component | Responsibility |
| --- | --- |
| Application | Create meaningful spans and safe attributes; propagate trace context. |
| Collector receiver | Accept OTLP from trusted internal services only. |
| Collector processors | Batch, limit memory, redact sensitive attributes, add resource attributes, and filter deliberately. |
| Collector exporters | Send complete LLM traces to Langfuse and all relevant telemetry to platform backends. |
| Langfuse | Map spans into LLM traces, observations, metrics, and evaluation workflows. |

Use Collector filtering when platform teams need centralized routing or cost control. Use SDK `should_export_span` when one Python/JS service needs local control over which spans Langfuse receives.

## Request/Response Shape for OTLP

Your application does not usually build OTLP payloads by hand, but this conceptual shape helps debug mapping:

```json
{
  "resourceSpans": [
    {
      "resource": {
        "attributes": {
          "service.name": "agent-service",
          "service.version": "sha-6f4c2d1",
          "deployment.environment.name": "prod"
        }
      },
      "scopeSpans": [
        {
          "spans": [
            {
              "name": "llm.generate_answer",
              "attributes": {
                "langfuse.trace.name": "rag.answer",
                "langfuse.user.id": "user_8f3a",
                "langfuse.observation.type": "generation",
                "gen_ai.provider.name": "openai",
                "gen_ai.request.model": "provider-model-name",
                "gen_ai.usage.input_tokens": 820,
                "gen_ai.usage.output_tokens": 164
              }
            }
          ]
        }
      ]
    }
  ]
}
```

The real OTLP encoding may be protobuf or JSON, but the semantic contract is the same: complete traces plus stable attributes.

## Mapping Pitfalls

- Sending OTLP/gRPC to Langfuse. Use OTLP/HTTP.
- Missing Basic Auth or using secret/public keys in the wrong order.
- Using `LANGFUSE_HOST` in new examples. Use `LANGFUSE_BASE_URL`.
- Setting trace attributes only on one span and expecting complete Langfuse metrics.
- Using legacy `gen_ai.system` for new instrumentation instead of `gen_ai.provider.name`.
- Sending raw prompts, retrieved documents, or user data without privacy review.
- Filtering out the root span in the Collector.
- Treating Langfuse as a metrics backend for all OTel metrics. Langfuse is trace and LLM workflow focused; send operational metrics to a metrics backend.

## Troubleshooting

| Symptom | Likely cause | Fix |
| --- | --- | --- |
| `4xx` from Langfuse OTLP endpoint | Wrong credentials, wrong base URL, missing Basic Auth, unsupported self-hosted version | Rebuild `AUTH_STRING` as `public:secret`, verify region/self-hosted URL, upgrade self-hosted Langfuse. |
| Exporter cannot connect or gets protocol errors | Using OTLP/gRPC to Langfuse | Use OTLP/HTTP JSON or protobuf endpoint. |
| Trace appears without model/cost usage | Missing GenAI or Langfuse generation attributes | Set `langfuse.observation.type`, model, parameters, and `gen_ai.usage.*` or `langfuse.observation.usage_details`. |
| Metadata is visible but not filterable | It was sent as ordinary OTel attributes | Use `langfuse.trace.metadata.<key>` or `langfuse.observation.metadata.<key>`. |
| Only leaf generations appear | Collector filter dropped root/business spans | Mark the root workflow as LLM-related and keep parent spans in the Langfuse pipeline. |
| Cross-service trace splits into multiple traces | W3C trace context not propagated/extracted | Instrument HTTP clients/servers or manually inject/extract context. |
| User/session filters miss spans | Attributes not propagated to all spans | Use baggage plus allowlist copying or SDK propagation helpers. |
| Sensitive headers/documents appear | Redaction processor missing or ordered too late | Redact before exporting to Langfuse and logs; add tests for representative payloads. |

## OTLP Production Checklist

- Use OTLP/HTTP, not OTLP/gRPC, for direct Langfuse ingestion.
- Set Basic Auth as `public_key:secret_key` and include `x-langfuse-ingestion-version=4`.
- Choose the correct regional or self-hosted `/api/public/otel` endpoint.
- Create a root workflow span and preserve it through Collector filters.
- Add `langfuse.trace.name`, user, session, release, version, tags, and filterable metadata.
- Map generation spans with model, parameters, input/output policy, token usage, and cost details where available.
- Propagate W3C trace context and only allowlisted baggage across services.
- Redact sensitive attributes before export.
- Route operational metrics to a metrics backend and logs to a log backend.
- Validate Collector config and test a full trace in staging before production.

## Official References

- Langfuse OpenTelemetry integration: <https://langfuse.com/integrations/native/opentelemetry>
- SDK overview and OTel foundation: <https://langfuse.com/docs/observability/sdk/overview>
- SDK advanced span filtering and masking: <https://langfuse.com/docs/observability/sdk/advanced-features>
