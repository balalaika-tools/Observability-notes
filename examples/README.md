# OpenTelemetry and Langfuse Examples

Last checked against the Langfuse and OpenTelemetry documentation on 2026-07-20.

These examples show how to combine OpenTelemetry and Langfuse in real LLM and agent systems.

Read the concept guides first:

- [../opentelemetry/README.md](../opentelemetry/README.md)
- [../langfuse/README.md](../langfuse/README.md)

Then use these examples as implementation templates.

| File | Scenario | Bootstrap profile |
| --- | --- | --- |
| [01_rag_with_otel_and_langfuse.md](01_rag_with_otel_and_langfuse.md) | Single-service RAG with Langfuse traces and OpenTelemetry metrics | Direct SDK tracing |
| [02_multi_service_agent.md](02_multi_service_agent.md) | Gateway calling a bounded tool-using agent | Direct SDK tracing in both services |
| [03_collector_prometheus_langfuse.md](03_collector_prometheus_langfuse.md) | Collector routing to Langfuse and Prometheus-compatible metrics | Centralized Collector export |

## Install

The examples use Python 3.11 or later. These ranges keep the APIs used here compatible while allowing patch and minor security updates:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install \
  'langfuse>=4.14,<5' \
  'openai>=1.92,<3' \
  'fastapi>=0.115,<1' \
  'uvicorn[standard]>=0.30,<1' \
  'httpx>=0.27,<1' \
  'pydantic>=2.8,<3' \
  'opentelemetry-api>=1.39,<2' \
  'opentelemetry-sdk>=1.39,<2' \
  'opentelemetry-exporter-otlp-proto-http>=1.39,<2' \
  'opentelemetry-exporter-otlp-proto-grpc>=1.39,<2' \
  'opentelemetry-instrumentation-fastapi>=0.60b0,<1' \
  'opentelemetry-instrumentation-httpx>=0.60b0,<1'
python -c 'import fastapi, httpx, langfuse, openai, opentelemetry.sdk'
```

Lock the resolved versions in the application rather than resolving this range during every deployment. OpenTelemetry stable packages and beta instrumentation packages are released as matched sets: SDK `1.39.x` corresponds to instrumentation `0.60b0`, for example. Upgrade them together and run the smoke request for the relevant example.

## Baseline Environment

Load credentials from secret management. Environment and release use Langfuse's first-class configuration fields:

```bash
export LANGFUSE_PUBLIC_KEY="pk-lf-..."
export LANGFUSE_SECRET_KEY="sk-lf-..."
export LANGFUSE_BASE_URL="https://cloud.langfuse.com"
export LANGFUSE_TRACING_ENVIRONMENT="dev"
export LANGFUSE_RELEASE="local"
export OPENAI_API_KEY="sk-proj-..."
```

Do not substitute custom `ENVIRONMENT` or `RELEASE` metadata keys. `LANGFUSE_TRACING_ENVIRONMENT` and `LANGFUSE_RELEASE` populate the fields used by Langfuse filters and release analytics. OpenTelemetry resources use the separate `deployment.environment.name` and `service.version` attributes.

## Bootstrap Profile A: Langfuse Direct Tracing and OTLP Metrics

Use this profile for examples 01 and 02. Langfuse owns trace batching and direct export. A separate OpenTelemetry `MeterProvider` sends unsampled operational metrics to a Collector. Do not install a second application `TracerProvider` for the same calls.

```python
# observability.py
import os

from langfuse import Langfuse
from opentelemetry import metrics
from opentelemetry.exporter.otlp.proto.http.metric_exporter import OTLPMetricExporter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import PeriodicExportingMetricReader
from opentelemetry.sdk.resources import Resource

langfuse = Langfuse(
    release=os.environ["LANGFUSE_RELEASE"],
    environment=os.environ["LANGFUSE_TRACING_ENVIRONMENT"],
    # Keep FastAPI/HTTPX spans needed to connect the two services in example 02.
    should_export_span=lambda span: True,
)

metric_reader = PeriodicExportingMetricReader(
    OTLPMetricExporter(
        endpoint=os.getenv(
            "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
            "http://localhost:4318/v1/metrics",
        )
    ),
    export_interval_millis=15_000,
)
meter_provider = MeterProvider(
    resource=Resource.create(
        {
            "service.name": os.environ["OTEL_SERVICE_NAME"],
            "service.version": os.environ["LANGFUSE_RELEASE"],
            "deployment.environment.name": os.environ[
                "LANGFUSE_TRACING_ENVIRONMENT"
            ],
        }
    ),
    metric_readers=[metric_reader],
)
metrics.set_meter_provider(meter_provider)


def shutdown_observability() -> None:
    langfuse.flush()
    meter_provider.shutdown()
    langfuse.shutdown()
```

Initialize this module before instrumenting FastAPI or HTTPX. Call `shutdown_observability()` once from the FastAPI lifespan shutdown path, or in a `finally` block for a script.

## Bootstrap Profile B: Centralized Collector Export

Use this profile for example 03. One application-owned OpenTelemetry provider sends traces and metrics to the Collector; the Collector owns Langfuse authentication, redaction, sampling, retry, and fan-out. Do not also initialize a Langfuse direct trace exporter in that process.

```bash
export OTEL_SERVICE_NAME="agent-service"
export OTEL_RESOURCE_ATTRIBUTES="service.version=${LANGFUSE_RELEASE},deployment.environment.name=${LANGFUSE_TRACING_ENVIRONMENT}"
export OTEL_EXPORTER_OTLP_ENDPOINT="http://otel-collector:4318"
export OTEL_EXPORTER_OTLP_PROTOCOL="http/protobuf"
export OTEL_SEMCONV_STABILITY_OPT_IN="http"
```

Configure one `TracerProvider` with an OTLP span exporter and one `MeterProvider` with an OTLP metric exporter, using [../opentelemetry/02_python_instrumentation.md](../opentelemetry/02_python_instrumentation.md). Shut down both providers once when the process exits.

## Safe Capture Default

Mask content before it reaches either an observation or a third-party auto-instrumentation layer. A practical default is an allowlist plus length limits:

```python
import re

SECRET = re.compile(r"(?i)(authorization|api[_-]?key|password)\s*[:=]\s*\S+")
EMAIL = re.compile(r"\b[\w.+-]+@[\w.-]+\.\w+\b")


def safe_text(value: str, *, limit: int = 2_000) -> str:
    value = SECRET.sub(r"\1=[REDACTED]", value)
    value = EMAIL.sub("[EMAIL]", value)
    return value[:limit]
```

The examples intentionally retain only these fields:

| Source | Retain | Drop or mask |
| --- | --- | --- |
| Question and answer | Masked, length-bounded text | Secrets, email addresses, excess text |
| Retrieved context | Document IDs, ranks, scores, approved short snippets | Full document bodies and ACL metadata |
| Tool calls | Bounded tool name, outcome, approved IDs | Credentials and raw downstream payloads |
| Account data | Tier and region from a small allowlist | Names, email, payment data, free-form fields |
| Feedback | Boolean value and masked comment up to 500 characters | Arbitrary caller metadata |
| Baggage | Opaque user/session IDs and bounded categories | Raw content and authorization decisions |
| Model messages | Masked copies when capture is allowed | Raw messages by default in restricted workflows |

Apply an export-stage masker as a second line of defense for spans created by integrations. Test the policy with representative secrets, PII, headers, exceptions, prompts, documents, tool results, and feedback.

## Avoid Duplicate Capture

There are four different mechanisms to distinguish:

```text
Langfuse observation helper ---- creates a Langfuse/OTel span
provider/framework integration -- may create a generation span for the same call
global span processor ----------- decides which process spans reach an exporter
Collector fan-out --------------- copies an existing span to destinations
```

Two exporters receiving the same span is intentional fan-out. Two instrumentations wrapping one model call creates two observations and double-counts usage and cost. Pick one generation owner per model call: a Langfuse wrapper, a framework callback, provider OTel instrumentation, or gateway instrumentation. Keep business/root spans from manual instrumentation and configure one global provider ownership model per process.

## Run and Verify

Example 01:

```bash
export OTEL_SERVICE_NAME=rag-service
uvicorn rag_app:app --port 8080
curl -sS http://localhost:8080/answer \
  -H 'content-type: application/json' \
  -d '{"question":"How do I reset SSO?"}'
```

Verify a `rag.answer` trace with retriever, generation, and evaluator children; first-class environment/release filters; model duration and retrieval metrics; and a `citation_present` score.

Example 02:

```bash
uvicorn agent_service:app --port 8081
AGENT_SERVICE_URL=http://localhost:8081 uvicorn gateway_service:app --port 8080
curl -sS http://localhost:8080/answer \
  -H 'authorization: Bearer <development-token>' \
  -H 'content-type: application/json' \
  -d '{"question":"How do I reset SSO?"}'
```

Verify one trace ID crosses both services, every tool call appears once with its actual outcome, the agent stops within its limit, and `agent.steps`, tool, and model metrics carry service/environment dimensions.

Example 03:

```bash
otelcol-contrib validate --config collector.yaml
promtool check rules alerts.yaml
```

Send the policy fixtures from that example. Verify selected complete traces in Langfuse, all operational metrics in the Prometheus-compatible backend, normalized label names, Collector health, and no secret canary values at either destination.

## Example Review Checklist

- Replace placeholder model names with models available in the provider account.
- Keep one primary capture path per model call.
- Return or persist the Langfuse trace ID with user-visible answers when feedback attaches later.
- Derive user, tenant, account, and session identity from authenticated server-side state.
- Flush Langfuse and shut down metric providers in scripts, tests, workers, and serverless functions.
