# OpenTelemetry Ingestion and Attribute Mapping

Last verified against official Langfuse OpenTelemetry documentation on 2026-07-20.

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


def configure_langfuse_otlp() -> TracerProvider:
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
    return provider
```

This example creates a direct exporter from one Python process to Langfuse. Retain the returned provider and flush it in short-lived work:

```python
provider = configure_langfuse_otlp()
try:
    run_job()
finally:
    provider.force_flush(timeout_millis=30_000)
    provider.shutdown()
```

`BatchSpanProcessor` exports asynchronously. Without `force_flush()` or `shutdown()`, the last batch from a CLI, test, worker, or serverless invocation can remain in memory when the process exits. In long-running services, call `shutdown()` once from the process lifespan hook. In production, many teams export to a Collector first so redaction, retry, batching, and routing policy are centralized.

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

A common production pattern sends all traces to the central tracing backend and complete LLM/agent traces to Langfuse. Route at the receiver or whole-trace boundary. Do not filter individual spans merely because they lack a generation attribute.

```yaml
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318
  otlp/llm:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4327
      http:
        endpoint: 0.0.0.0:4328

processors:
  memory_limiter:
    check_interval: 1s
    limit_mib: 512
  batch:
    timeout: 5s
    send_batch_size: 1024
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
      receivers: [otlp, otlp/llm]
      processors: [memory_limiter, batch]
      exporters: [otlp/main_traces]
    traces/langfuse:
      receivers: [otlp/llm]
      processors: [memory_limiter, batch]
      exporters: [otlphttp/langfuse]
```

LLM services send all spans to port 4327/4328; ordinary services use 4317/4318. Both receiver streams reach the main backend, while only the complete LLM-service stream reaches Langfuse. Protect both receivers on the private network. If one service mixes LLM and non-LLM workflows and volume requires selective routing, use trace-ID-aware routing plus tail/whole-trace policy. A root marker such as `llm.workflow=true` must be propagated to all spans or evaluated by a trace-aware component; a span-level filter on that marker still drops unmarked parents and children.

Architecture responsibilities:

| Component | Responsibility |
| --- | --- |
| Application | Create meaningful spans and safe attributes; propagate trace context. |
| Collector receiver | Accept OTLP from trusted internal services only. |
| Collector processors | Batch, limit memory, redact sensitive attributes, add resource attributes, and filter deliberately. |
| Collector exporters | Send complete LLM traces to Langfuse and all relevant telemetry to platform backends. |
| Langfuse | Map spans into LLM traces, observations, metrics, and evaluation workflows. |

Use Collector filtering when platform teams need centralized routing or cost control. Use SDK `should_export_span` when one Python/JS service needs local control over which spans Langfuse receives.

## Raw-OTLP Dataset Experiment

Use the Langfuse experiment runner in Python or TypeScript when possible. For another runtime that emits raw OTLP, model each dataset item as a separate root trace. Never create one enclosing span for the whole experiment.

The following Python example shows the wire contract. `BaggageSpanProcessor` copies only the listed experiment keys to new spans:

```python
import json
import os
from base64 import b64encode
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
from opentelemetry import baggage, context, trace
from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.trace import SpanProcessor, TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor


class ExperimentBaggageSpanProcessor(SpanProcessor):
    ALLOWED_PREFIXES = ("langfuse.experiment.",)
    ALLOWED_KEYS = {"langfuse.environment"}

    def on_start(self, span, parent_context=None):
        ctx = parent_context or context.get_current()
        for key, value in baggage.get_all(context=ctx).items():
            if key in self.ALLOWED_KEYS or key.startswith(self.ALLOWED_PREFIXES):
                span.set_attribute(key, value)

    def on_end(self, span):
        pass

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30_000):
        return True


@dataclass(frozen=True)
class Item:
    id: str
    version: datetime
    input: dict
    expected_output: dict
    metadata: dict


auth = b64encode(
    f"{os.environ['LANGFUSE_PUBLIC_KEY']}:{os.environ['LANGFUSE_SECRET_KEY']}".encode()
).decode()
provider = TracerProvider()
provider.add_span_processor(ExperimentBaggageSpanProcessor())
provider.add_span_processor(
    BatchSpanProcessor(
        OTLPSpanExporter(
            endpoint=(
                f"{os.environ['LANGFUSE_BASE_URL'].rstrip('/')}"
                "/api/public/otel/v1/traces"
            ),
            headers={
                "Authorization": f"Basic {auth}",
                "x-langfuse-ingestion-version": "4",
            },
        )
    )
)
trace.set_tracer_provider(provider)
tracer = provider.get_tracer("raw-otel-experiment")
dataset_version = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)

experiment_ctx = context.Context()  # No active span: every item starts a new trace.
for key, value in {
    "langfuse.experiment.id": "exp-support-rag-v18",
    "langfuse.experiment.name": "support-rag-prompt-v18",
    "langfuse.experiment.dataset.id": "dataset-id-from-langfuse",
    "langfuse.experiment.description": "Pinned dataset regression",
    "langfuse.experiment.metadata.candidate": "prompt-v18",
    "langfuse.environment": "experiment",
}.items():
    experiment_ctx = baggage.set_baggage(key, value, context=experiment_ctx)


def run_item(item: Item) -> None:
    root = tracer.start_span("experiment-item", context=experiment_ctx)
    trace_id = format(root.get_span_context().trace_id, "032x")
    root_span_id = format(root.get_span_context().span_id, "016x")
    item_version = item.version.isoformat()

    item_attributes = {
        "langfuse.experiment.item.id": item.id,
        "langfuse.experiment.item.root_observation_id": root_span_id,
        "langfuse.experiment.item.version": item_version,
    }
    for key, value in item_attributes.items():
        root.set_attribute(key, value)
    root.set_attribute("langfuse.observation.input", json.dumps(item.input))
    root.set_attribute(
        "langfuse.experiment.item.expected_output",
        json.dumps(item.expected_output),
    )
    root.set_attribute(
        "langfuse.experiment.item.metadata.segment",
        str(item.metadata["segment"]),
    )

    item_ctx = experiment_ctx
    for key, value in item_attributes.items():
        item_ctx = baggage.set_baggage(key, value, context=item_ctx)
    item_ctx = trace.set_span_in_context(root, item_ctx)

    token = context.attach(item_ctx)
    try:
        # Instrumented child generations/tools inherit experiment and item baggage.
        output = run_instrumented_task(item.input)
        root.set_attribute("langfuse.observation.output", json.dumps(output))
    finally:
        context.detach(token)
        root.end()

    # Scores target both the item trace and its root observation.
    httpx.post(
        f"{os.environ['LANGFUSE_BASE_URL']}/api/public/scores",
        auth=(os.environ["LANGFUSE_PUBLIC_KEY"], os.environ["LANGFUSE_SECRET_KEY"]),
        json={
            "traceId": trace_id,
            "observationId": root_span_id,
            "name": "required_citations_present",
            "value": 1,
            "dataType": "BOOLEAN",
        },
        timeout=10,
    ).raise_for_status()


try:
    for item in pinned_items:
        assert item.version == dataset_version
        run_item(item)
finally:
    provider.force_flush(timeout_millis=30_000)
    provider.shutdown()
```

Register `ExperimentBaggageSpanProcessor` before starting any spans. For a Langfuse-managed dataset, use the dataset and item IDs returned by Langfuse and set every item's `version` to the exact fetched dataset version timestamp. Local datasets may use stable local correlation IDs but must omit item version. Input, actual output, and expected output belong on the item root; experiment/item identity belongs in baggage so child spans participate in the same experiment.

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
- Using attribute path segments named `__proto__`, `constructor`, or `prototype`. Langfuse silently drops those segments; rename them during normalization, for example to `proto_name`, `constructor_name`, or `prototype_name`.
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
